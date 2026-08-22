"""Spec 02 — BRAIN. Transcript + trimmed history → streamed response tokens.

Cascade: every configured Groq key × 70B → every key × 8B → ollama → canned.
Never silent, abortable between any two chunks.
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
from guests import Guest
from nethealth import health

log = logging.getLogger("brain")

_NORM_RE = re.compile(r"[^\w ]")  # \w keeps Devanagari/Urdu letters, not just a-z

# The search model has a fixed ~1.2s orchestration tax even when it succeeds
# (measured live) — worth paying for "who's the current PM" but not for "yes",
# "no", or "can you hear me". Only route through it when the text actually
# looks like it needs a live lookup; everything else skips straight to the
# fast/reliable model, same latency as before this feature existed.
_SEARCH_HINT_RE = re.compile(
    r"\b(current|currently|latest|right now|today|this (?:year|week|month)|"
    r"news|weather|score|price of|stock|exchange rate|"
    r"president|prime minister|chief minister|governor|ceo of|"
    r"who\s+(?:is|won)|what(?:'s|\s+is)\s+the\s+latest|"
    r"search|look\s?up|google)\b",
    re.IGNORECASE,
)


def _needs_search(text: str) -> bool:
    return bool(_SEARCH_HINT_RE.search(text))

# Failures that mean "the network is the problem" — rotating keys or models
# won't help, so the cascade should jump straight to the local model.
# (TimeoutError covers asyncio.wait_for; APIConnectionError includes the
# groq SDK's timeout subclass; TransportError is httpx's connect/read family.)
_NETWORK_ERRORS = (TimeoutError, ConnectionError, APIConnectionError, httpx.TransportError)


def _norm(text: str) -> str:
    return _NORM_RE.sub("", text.lower()).strip()


# Strips a leading hesitation ("uh, my name is...") THEN a lead-in phrase, so
# "my name is Sarah" / "I'm Ali" / "it's Zara" / "uh my name is John" / a bare
# "Zara" all resolve the same way.
_HESITATION_PREFIX_RE = re.compile(r"^\s*(?:uh+|um+|hmm+|well|so)[\s,]+", re.IGNORECASE)
_NAME_LEADIN_RE = re.compile(
    r"^\s*(?:my name is|my name's|i am|i'm|its|it's|this is|call me)\s+",
    re.IGNORECASE,
)
# Hesitation/junk a mic could easily pick up instead of an actual name —
# without this, a stray "uh" or "no" gets title-cased into a fake "name".
_NAME_STOPWORDS = {
    "uh", "um", "umm", "uhh", "huh", "hmm", "well", "no", "yes", "nope",
    "yeah", "ok", "okay", "hi", "hello", "hey", "what", "sorry", "who",
}


def _extract_name(text: str) -> str | None:
    """Best-effort name pull from a raw STT transcript. None means "couldn't
    tell" — caller decides whether to retry or give up, never guesses wrong
    on purpose. Caps at two words (first + last name) so a rambling answer
    doesn't get swallowed whole as a "name"."""
    stripped = _HESITATION_PREFIX_RE.sub("", text)
    stripped = _NAME_LEADIN_RE.sub("", stripped).strip(" .,!?")
    words = stripped.split()[:2]
    if not words:
        return None
    if words[0].lower() in _NAME_STOPWORDS:
        return None
    name = " ".join(words).title()
    if len(name) < 2:
        return None
    return name


class Brain:
    def __init__(self, bus: Bus, config) -> None:
        self.bus = bus
        self.cfg = config
        self.last_provider = "none"    # which layer answered the last turn (for REPL/debug)

        self._history: list[dict] = []
        self._visitor_name: str | None = None
        self._abort = asyncio.Event()
        self._last_interaction = time.monotonic()

        # One client per key — as many as GROQ_API_KEY/_2/_3/... in .env,
        # separate quota buckets, rotate on failure (see config._collect_keys)
        self._groq_clients = [
            AsyncGroq(api_key=key, timeout=config.GROQ_TIMEOUT_S, max_retries=0)
            for key in config.GROQ_API_KEYS
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

        messages = (
            [{"role": "system", "content": self._with_visitor_name(self.cfg.SYSTEM_PROMPT)}]
            + self._history
            + [{"role": "user", "content": text}]
        )
        self._history.append({"role": "user", "content": text})

        reply_parts: list[str] = []
        async for token in self._stream(messages, text, reply_parts):
            yield token

        reply = "".join(reply_parts)
        if reply.strip():
            # aborted partials count too — it's what the visitor actually heard
            self._history.append({"role": "assistant", "content": reply})
        self._history = self._history[-(self.cfg.HISTORY_TURNS * 2):]  # hard trim, every turn
        self._last_interaction = time.monotonic()

    async def welcome_special_guest(self, guest: Guest) -> AsyncIterator[str]:
        """Same cascade/filler machinery as respond(), but for the one-off
        VIP-arrival line: no prior history, guest bio folded into the user
        turn (see guests.py/main.py's _handle_name_reply)."""
        self._abort.clear()
        self.set_visitor_name(guest.name)  # canonical roster spelling, not the raw transcript
        prompt = self.cfg.SPECIAL_GUEST_PROMPT_TEMPLATE.format(
            name=guest.name, title=guest.title,
            background=guest.background, contributions=guest.contributions,
        )
        messages = [
            {"role": "system", "content": self._with_visitor_name(self.cfg.SYSTEM_PROMPT)},
            {"role": "user", "content": prompt},
        ]

        reply_parts: list[str] = []
        async for token in self._stream(
            messages, prompt, reply_parts,
            max_tokens=self.cfg.SPECIAL_GUEST_MAX_TOKENS, allow_search=False,
        ):
            yield token

        reply = "".join(reply_parts)
        self._history = [{"role": "user", "content": prompt}]
        if reply.strip():
            self._history.append({"role": "assistant", "content": reply})
        self._last_interaction = time.monotonic()

    async def _stream(
        self, messages: list[dict], user_text: str, reply_parts: list[str],
        max_tokens: int | None = None, allow_search: bool = True,
    ) -> AsyncIterator[str]:
        """Filler-then-cascade streaming shared by respond() and
        welcome_special_guest(). Appends every real model token (never
        fillers) to reply_parts so the caller can build history afterward.
        max_tokens overrides cfg.MAX_TOKENS for callers needing more room
        (e.g. the special-guest welcome)."""
        # Filler goes out BEFORE the LLM call — masks ~800ms of latency.
        # Trailing "..." makes voice's chunker flush it instantly from cache.
        last_filler = random.choice(self.cfg.FILLERS)
        yield last_filler + "... "

        it = self._cascade(messages, user_text, max_tokens, allow_search).__aiter__()
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
                reply_parts.append(first_token)
                yield first_token
            async for token in it:
                if self._abort.is_set():
                    break
                reply_parts.append(token)
                yield token

    def abort(self) -> None:
        """Barge-in: stop mid-stream. respond() checks this between chunks."""
        self._abort.set()

    def set_visitor_name(self, name: str) -> None:
        """Threaded on every turn — the cloud path gets it via the system
        prompt (_with_visitor_name); the Ollama path gets it folded into the
        user turn instead (see _ollama_stream) since small models measurably
        ignore facts injected into the system prompt mid-conversation — the
        exact bug already found and fixed once this session for
        OLLAMA_CONTEXT_HINT. Verified live: system-prompt injection alone
        made the 0.5b model answer "what's my name" with its OWN name
        instead of the visitor's; the user-turn version fixed it. Not kept
        in _history (which gets hard-trimmed every turn) so it survives a
        long session regardless of which path answers."""
        self._visitor_name = name

    def _with_visitor_name(self, base_prompt: str) -> str:
        if not self._visitor_name:
            return base_prompt
        return f"{base_prompt}\n\nThe visitor's name is {self._visitor_name}."

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
        self._visitor_name = None

    # ── provider cascade ─────────────────────────────────────────────────────

    async def _tavily_search(self, query: str) -> str | None:
        """Best-effort live search: real results get folded into the Groq
        request as extra context (see _cascade), so the reliable model does
        the actual answering instead of trusting a search-orchestration model
        to get everything right in one shot. Rotates across TAVILY_API_KEYS —
        each one a separate free-tier account/quota — on any failure, same
        idea as the Groq key rotation below. Never raises, never touches the
        shared breaker (Tavily being down isn't evidence the internet/Groq is
        down); every key failing (or none configured) just returns None."""
        for key in self.cfg.TAVILY_API_KEYS:
            # Everything that can raise — the request AND the response
            # parsing — stays inside this try. A malformed/unexpected
            # response shape is just as much a "this key/attempt failed" case
            # as a network error; it must never escape and crash the cascade.
            try:
                async with httpx.AsyncClient(timeout=self.cfg.TAVILY_TIMEOUT_S) as client:
                    resp = await client.post(
                        "https://api.tavily.com/search",
                        headers={"Authorization": f"Bearer {key}"},
                        json={
                            "query": query,
                            "search_depth": "basic",
                            "max_results": self.cfg.TAVILY_MAX_RESULTS,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                results = data.get("results") or []
                if not results:
                    return None
                lines = [f"- {r.get('title', '')}: {r.get('content', '')}" for r in results]
                return (
                    "Live web search results for the visitor's question — use these "
                    "facts if relevant, answer naturally in one sentence like you "
                    "already know it, don't dump a list or mention 'search results':\n"
                    + "\n".join(lines)
                )
            except Exception as e:
                log.warning("tavily search failed (key ...%s): %r — trying next key",
                            key[-4:], e)
                continue
        return None  # every key failed, or none configured

    def _attempts(self) -> list[tuple[int, str]]:
        """Rotate key first, then downgrade model: (key_idx, model) pairs."""
        keys = range(len(self._groq_clients))
        return [(k, m) for m in (self.cfg.GROQ_MODEL, self.cfg.GROQ_FALLBACK) for k in keys]

    async def _cascade(
        self, messages: list[dict], user_text: str, max_tokens: int | None = None,
        allow_search: bool = True,
    ) -> AsyncIterator[str]:
        if self.cfg.LLM_BACKEND == "groq" and health.ok:
            # Live search, best-effort: only for questions that look like they
            # need one (_needs_search), and a miss/timeout here never blocks or
            # degrades the turn — it just answers without the extra context.
            # Belt-and-suspenders try/except: _tavily_search is written to
            # never raise, but this step must be unable to break the cascade
            # no matter what — an answer with no search context beats no
            # answer at all. allow_search=False skips this entirely — the
            # special-guest welcome's prompt text contains titles like "Chief
            # Minister"/"Governor" that spuriously match _SEARCH_HINT_RE even
            # though every fact needed is already in the prompt.
            if allow_search and self.cfg.TAVILY_API_KEYS and _needs_search(user_text):
                try:
                    context = await self._tavily_search(user_text)
                except Exception as e:
                    log.warning("tavily search step crashed unexpectedly: %r — answering without it", e)
                    context = None
                if context:
                    messages = messages[:-1] + [
                        {"role": "system", "content": context},
                        messages[-1],
                    ]

            for key_idx, model in self._attempts():
                started = False
                try:
                    async for token in self._groq_stream(key_idx, model, messages, max_tokens):
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
                async for token in self._ollama_stream(messages, max_tokens):
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

    async def _groq_stream(
        self, key_idx: int, model: str, messages: list[dict], max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        if self.simulate_offline:
            raise ConnectionError("simulated offline (--simulate-offline)")
        raw = await asyncio.wait_for(
            self._groq_clients[key_idx].chat.completions.with_raw_response.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=max_tokens or self.cfg.MAX_TOKENS,  # latency guard + quota conservation
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

    async def _ollama_stream(
        self, messages: list[dict], max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        # Reshape for the CPU: compact system prompt, last N turns only —
        # every token the local model re-reads is first-token latency.
        n = getattr(self.cfg, "OLLAMA_HISTORY_TURNS", self.cfg.HISTORY_TURNS) * 2
        tail = messages[1:][-(n + 1):]  # history + the trailing user message
        # Both folded into the USER message, never the system prompt — small
        # models ignore facts injected into the system prompt mid-conversation.
        # The name needed an unusually blunt phrasing: softer wording ("the
        # visitor's name is X") lost every time to the model's own trained
        # "I am Rebotic" identity on a direct "what's my name" question —
        # verified live across several phrasings before landing on this one
        # (4/4 correct vs. 0/3 for the natural-language versions). The name
        # applies from turn one of a session; the context hint only matters
        # once there's history to point back at.
        hints = ([f'Visitor name: {self._visitor_name}. Rule: never answer a '
                  f'"my name" question with Rebotic. Always answer with '
                  f'{self._visitor_name}.']
                 if self._visitor_name else [])
        context_hint = getattr(self.cfg, "OLLAMA_CONTEXT_HINT", "")
        if context_hint and len(tail) > 1:
            hints.append(context_hint)
        if hints and tail:
            combined = " ".join(hints)
            tail = tail[:-1] + [{"role": "user", "content": f"({combined})\n{tail[-1]['content']}"}]
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
                        "num_predict": max_tokens or self.cfg.MAX_TOKENS,
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
