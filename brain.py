"""Spec 02 — BRAIN. Transcript + trimmed history → streamed response tokens.

Cascade: key1+70B → key2+70B → key1+8B → key2+8B → ollama → canned.
Six lives, never silent, abortable between any two chunks.
"""

import asyncio
import difflib
import json
import logging
import random
import re
import time
from typing import AsyncIterator

import httpx
from groq import APIConnectionError, AsyncGroq

from bus import Bus
from nethealth import health

log = logging.getLogger("brain")

_NORM_RE = re.compile(r"[^\w ]")  # \w keeps Devanagari/Urdu letters, not just a-z

# Failures that mean "the network is the problem" — rotating keys or models
# won't help, so the cascade should jump straight to the local model.
# (TimeoutError covers asyncio.wait_for; APIConnectionError includes the
# groq SDK's timeout subclass; TransportError is httpx's connect/read family.)
_NETWORK_ERRORS = (TimeoutError, ConnectionError, APIConnectionError, httpx.TransportError)


def _norm(text: str) -> str:
    return _NORM_RE.sub("", text.lower()).strip()


class Brain:
    def __init__(self, bus: Bus, config) -> None:
        self.bus = bus
        self.cfg = config
        self.last_provider = "none"    # which layer answered the last turn (for REPL/debug)

        self._history: list[dict] = []
        self._abort = asyncio.Event()
        self._last_interaction = time.monotonic()

        # One client per key — separate quota buckets, rotate on failure
        self._groq_clients = [
            AsyncGroq(api_key=key, timeout=config.GROQ_TIMEOUT_S, max_retries=0)
            for key in (config.GROQ_API_KEY, config.GROQ_API_KEY_2)
            if key
        ]

    # ── interface ────────────────────────────────────────────────────────────

    @property
    def simulate_offline(self) -> bool:
        """Test hook, proxied to the shared breaker so voice goes offline too."""
        return health.simulate_offline

    @simulate_offline.setter
    def simulate_offline(self, value: bool) -> None:
        health.simulate_offline = value

    @property
    def cloud_ok(self) -> bool:
        return health.ok

    async def respond(self, text: str) -> AsyncIterator[str]:
        now = time.monotonic()
        if now - self._last_interaction > self.cfg.IDLE_RESET_S:
            self.reset()  # one visitor's weird chat shouldn't confuse the next
        self._last_interaction = now
        self._abort.clear()

        # Filler goes out BEFORE the LLM call — masks ~800ms of latency.
        # Trailing "..." makes voice's chunker flush it instantly from cache.
        last_filler = random.choice(self.cfg.FILLERS)
        yield last_filler + "... "

        messages = (
            [{"role": "system", "content": self.cfg.SYSTEM_PROMPT}]
            + self._history
            + [{"role": "user", "content": text}]
        )
        self._history.append({"role": "user", "content": text})

        reply = ""
        it = self._cascade(messages, text).__aiter__()
        # Peek for the first token with a short deadline. asyncio.wait (unlike
        # wait_for) does NOT cancel the pending call on timeout, so a slow
        # cascade (cold Ollama, etc.) keeps running underneath while we chain
        # in more fillers instead of leaving dead air after the first one —
        # that silence is exactly what read as "the model failed".
        task = asyncio.ensure_future(it.__anext__())
        chains = 0
        while not self._abort.is_set():
            done, _ = await asyncio.wait({task}, timeout=self.cfg.FILLER_CHAIN_S)
            if task in done:
                break
            if chains < self.cfg.FILLER_CHAIN_MAX:
                chains += 1
                last_filler = random.choice(
                    [f for f in self.cfg.FILLERS if f != last_filler] or self.cfg.FILLERS)
                yield last_filler + "... "
            # else: stop babbling past the cap, keep waiting quietly

        if self._abort.is_set():
            task.cancel()
        else:
            try:
                first_token = task.result()
            except StopAsyncIteration:
                first_token = None
            if first_token is not None:
                reply += first_token
                yield first_token
            async for token in it:
                if self._abort.is_set():
                    break
                reply += token
                yield token

        if reply.strip():
            # aborted partials count too — it's what the visitor actually heard
            self._history.append({"role": "assistant", "content": reply})
        self._history = self._history[-(self.cfg.HISTORY_TURNS * 2):]  # hard trim, every turn
        self._last_interaction = time.monotonic()

    def abort(self) -> None:
        """Barge-in: stop mid-stream. respond() checks this between chunks."""
        self._abort.set()

    def _ollama_system_prompt(self) -> str:
        return getattr(self.cfg, "OLLAMA_SYSTEM_PROMPT", self.cfg.SYSTEM_PROMPT)

    async def warm_ollama(self) -> None:
        """Preload the fallback model AND pre-compute its system prompt — CPU
        prompt eval is slow, so evaluating the prompt now primes llama.cpp's
        prefix cache and a mid-demo outage doesn't cost a cold first token.
        Fire-and-forget at boot."""
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                await client.post(
                    f"{self.cfg.OLLAMA_URL}/api/chat",
                    json={
                        "model": self.cfg.OLLAMA_MODEL,
                        "messages": [{"role": "system", "content": self._ollama_system_prompt()}],
                        "stream": False,
                        "keep_alive": "6h",
                        "options": {"num_predict": 1},
                    },
                )
            log.info("ollama %s warm, system prompt cached (kept alive 6h)",
                     self.cfg.OLLAMA_MODEL)
        except Exception as e:
            log.info("ollama warm-up skipped: %r", e)

    def reset(self) -> None:
        self._history.clear()

    # ── provider cascade ─────────────────────────────────────────────────────

    def _attempts(self) -> list[tuple[int, str]]:
        """Rotate key first, then downgrade model: (key_idx, model) pairs."""
        keys = range(len(self._groq_clients))
        return [(k, m) for m in (self.cfg.GROQ_MODEL, self.cfg.GROQ_FALLBACK) for k in keys]

    async def _cascade(self, messages: list[dict], user_text: str) -> AsyncIterator[str]:
        if self.cfg.LLM_BACKEND == "groq" and health.ok:
            for key_idx, model in self._attempts():
                started = False
                try:
                    async for token in self._groq_stream(key_idx, model, messages):
                        started = True
                        yield token
                    self.last_provider = f"groq:{model} key{key_idx + 1}"
                    return
                except _NETWORK_ERRORS as e:
                    log.warning("groq key%d %s network error: %r — switching to local",
                                key_idx + 1, model, e)
                    health.mark_down()
                    if started:
                        return  # died mid-sentence — don't restart and repeat ourselves
                    break  # other keys share the same dead network — go local now
                except Exception as e:
                    log.warning("groq key%d %s failed: %r", key_idx + 1, model, e)
                    if started:
                        return
                if self._abort.is_set():
                    return

        if self.cfg.LLM_BACKEND in ("groq", "ollama"):
            started = False
            try:
                async for token in self._ollama_stream(messages):
                    started = True
                    yield token
                self.last_provider = f"ollama:{self.cfg.OLLAMA_MODEL}"
                return
            except Exception as e:
                log.warning("ollama failed: %r", e)
                if started:
                    return
            if self._abort.is_set():
                return

        self.last_provider = "canned"
        yield self._canned(user_text)

    async def _groq_stream(self, key_idx: int, model: str, messages: list[dict]) -> AsyncIterator[str]:
        if self.simulate_offline:
            raise ConnectionError("simulated offline (--simulate-offline)")
        raw = await asyncio.wait_for(
            self._groq_clients[key_idx].chat.completions.with_raw_response.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=self.cfg.MAX_TOKENS,  # latency guard + quota conservation
                temperature=self.cfg.TEMPERATURE,
            ),
            timeout=self.cfg.GROQ_TIMEOUT_S,
        )
        h = raw.headers
        log.info(
            "groq key%d %s | remaining req=%s tok=%s reset=%s",
            key_idx + 1, model,
            h.get("x-ratelimit-remaining-requests"),
            h.get("x-ratelimit-remaining-tokens"),
            h.get("x-ratelimit-reset-requests"),
        )
        stream = await raw.parse()
        # Manual iteration so a stalled connection (slow internet) can't hang a
        # turn — a token gap over GROQ_TOKEN_TIMEOUT_S raises and trips the breaker.
        it = stream.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(anext(it), self.cfg.GROQ_TOKEN_TIMEOUT_S)
            except StopAsyncIteration:
                return
            if self._abort.is_set():
                await stream.close()
                return
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def _ollama_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        # Reshape for the CPU: compact system prompt, last N turns only —
        # every token the local model re-reads is first-token latency.
        n = getattr(self.cfg, "OLLAMA_HISTORY_TURNS", self.cfg.HISTORY_TURNS) * 2
        tail = messages[1:][-(n + 1):]  # history + the trailing user message
        hint = getattr(self.cfg, "OLLAMA_CONTEXT_HINT", "")
        if hint and len(tail) > 1:  # only useful when there IS history to point at
            # folded into the user message — small models ignore mid-chat system turns
            tail = tail[:-1] + [{"role": "user", "content": f"({hint})\n{tail[-1]['content']}"}]
        messages = [{"role": "system", "content": self._ollama_system_prompt()}] + tail
        timeout = httpx.Timeout(getattr(self.cfg, "OLLAMA_TIMEOUT_S", 30), connect=2)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.cfg.OLLAMA_URL}/api/chat",
                json={
                    "model": self.cfg.OLLAMA_MODEL,
                    "messages": messages,
                    "stream": True,
                    "keep_alive": "6h",  # a chat without this resets expiry to 5min
                    "options": {
                        "num_predict": self.cfg.MAX_TOKENS,
                        # cooler than the cloud tier: the small model needs
                        # every bit of focus it has to stay on-context
                        "temperature": getattr(self.cfg, "OLLAMA_TEMPERATURE", 0.4),
                    },
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if self._abort.is_set():
                        return
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if data.get("done"):
                        return

    def _canned(self, user_text: str) -> str:
        """Last life. Fuzzy-match the transcript against scripted answers."""
        q = _norm(user_text)
        for key, answer in self.cfg.CANNED.items():
            if key in q:
                return answer
        close = difflib.get_close_matches(q, self.cfg.CANNED.keys(), n=1, cutoff=0.6)
        return self.cfg.CANNED[close[0]] if close else self.cfg.CANNED_DEFAULT


# ── standalone REPL ───────────────────────────────────────────────────────────

async def _repl() -> None:
    import signal
    import sys

    import config

    logging.basicConfig(level=logging.INFO, format="%(levelname).1s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    brain = Brain(Bus(), config)
    brain.simulate_offline = "--simulate-offline" in sys.argv
    if brain.simulate_offline:
        print("(Groq path disabled — testing the fallback chain)")
    print("Type text, watch tokens. Ctrl-C aborts the stream, Ctrl-D quits.")
    print("/offline and /online simulate the internet dropping mid-conversation.\n")

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, brain.abort)
    warm = asyncio.create_task(brain.warm_ollama())  # noqa: F841 — keep a ref

    while True:
        try:
            text = await loop.run_in_executor(None, input, "you> ")
        except EOFError:
            print()
            return
        if not text.strip():
            continue
        if text.strip() == "/offline":
            brain.simulate_offline = True
            print("     (internet cut — next turn should trip the breaker)")
            continue
        if text.strip() == "/online":
            brain.simulate_offline = False
            print("     (internet restored — probe should reconnect within "
                  f"{config.CLOUD_PROBE_INTERVAL_S}s)")
            continue

        gen = brain.respond(text)
        filler = await anext(gen)
        print(f"     ({filler.strip()})")
        t0 = time.perf_counter()
        ttft_ms = None
        async for token in gen:
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - t0) * 1000
            print(token, end="", flush=True)
        if ttft_ms is None:
            print("(aborted before first token)", end="")
        print(f"\n     — {brain.last_provider}"
              + (f" · TTFT {ttft_ms:.0f}ms" if ttft_ms is not None else ""))


if __name__ == "__main__":
    asyncio.run(_repl())
