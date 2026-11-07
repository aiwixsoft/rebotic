"""Wires hearing → brain → voice through the bus, per PROJECT.md's flow.

IDLE → DETECT → TRANSCRIBE → END_TURN → THINK → GENERATE → SPEAK → IDLE
with INTERRUPT possible any time the bot is generating or speaking.
Modules never touch each other — every transition happens here.
"""

import argparse
import asyncio
import logging
import signal
import time
from typing import AsyncIterator

import config
import guests
from brain import Brain, _extract_name
from bus import Bus
from hearing import Hearing
from robot import Robot
from voice import Voice

log = logging.getLogger("main")


class Rebotic:
    def __init__(self, latency: bool = False) -> None:
        self.latency = latency
        self.bus = Bus()
        self.robot = Robot()
        self.hearing = Hearing(self.bus, config)
        self.brain = Brain(self.bus, config)
        self.voice = Voice(self.bus, self.robot, config)
        self.guests = guests.load(config.SPECIAL_GUEST_FILE)

        self._speak_task: asyncio.Task | None = None
        self._interrupted = False
        # Guards against a stale `final` — hearing.py's online-STT lookup is
        # backgrounded and can take up to STT_ONLINE_TIMEOUT_S, long enough for
        # a newer utterance's reply to already be playing by the time an older
        # one's lookup finishes. Only ever act on a final whose turn_id is the
        # newest seen; an older, late-arriving one is dropped instead of
        # incorrectly interrupting the newer reply already in progress.
        self._latest_turn_id = 0
        # Visitor sessions (PTT tap, see hearing.py's _cancel_as_tap)
        self._awaiting_name = False
        self._name_attempts = 0
        # per-turn latency marks
        self._t_final = 0.0
        self._t_first_token: float | None = None
        self._t_first_audio: float | None = None

        if latency:
            # tap amp publishes to catch the exact moment audio becomes audible
            orig_amp = self.robot.amp
            def amp(v: int) -> None:
                if v > 0 and self._t_first_audio is None:
                    self._t_first_audio = time.perf_counter()
                orig_amp(v)
            self.robot.amp = amp

    # ── event loop ───────────────────────────────────────────────────────────

    async def run(self) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, stop.set)

        # preload the offline-fallback model and the neural filler cache while we greet
        self._warm_task = asyncio.create_task(self.brain.warm_ollama())
        self._warm_tts_task = asyncio.create_task(self.voice.warm_online())

        # GREET — before the mic opens, so the speaker can't barge itself in
        self.robot.state("idle")
        await self.voice.speak_stream(self._say(config.GREETING))
        self.robot.state("idle")

        hear_task = asyncio.create_task(self.hearing.start())
        print("listening — speak, or hold SPACE for push-to-talk")
        consumer = asyncio.create_task(self._consume())
        await stop.wait()

        # graceful shutdown: stop mic, kill audio, close the jaw, disconnect
        log.info("shutting down")
        consumer.cancel()
        if self._speak_task and not self._speak_task.done():
            self.brain.abort()
            self.voice.kill()
            self._speak_task.cancel()
        await self.hearing.stop()
        hear_task.cancel()
        self.voice.close()
        self.robot.amp(0)
        self.robot.state("idle")
        self.robot.close()

    async def _consume(self) -> None:
        while True:
            ev = await self.bus.get()
            if ev.type == "speech_start":
                pass   # only idle/speak exist now -- nothing to publish here
            elif ev.type == "partial":
                log.debug("partial: %s", ev.data["text"])
            elif ev.type == "barge_in":
                self._interrupt()
            elif ev.type == "new_session":
                self._start_new_session()
            elif ev.type == "final":
                turn_id = ev.data.get("turn_id", 0)
                if turn_id < self._latest_turn_id:
                    # a backgrounded online-STT lookup for an older utterance
                    # finally resolved after a newer reply already started —
                    # drop it rather than let it interrupt the newer one.
                    log.debug("dropping stale final (turn %d behind %d): %r",
                              turn_id, self._latest_turn_id, ev.data["text"])
                    continue
                self._latest_turn_id = turn_id
                text = ev.data["text"]
                gender = ev.data.get("gender", "unknown")
                self.voice.set_gender(gender)  # unknown keeps the current voice
                print(f"you said: {text}  (voice: {gender})")
                if self._speak_task and not self._speak_task.done():
                    self._interrupt()  # safety — shouldn't happen without barge_in
                if self._awaiting_name:
                    self._speak_task = asyncio.create_task(self._handle_name_reply(text))
                else:
                    self._speak_task = asyncio.create_task(self._turn(text))

    @staticmethod
    async def _say(text: str) -> AsyncIterator[str]:
        yield text

    def _interrupt(self) -> None:
        """INTERRUPT: kill generation + audio, back to DETECT (attend)."""
        log.info("barge-in — killing speech")
        self._interrupted = True
        self.brain.abort()
        self.voice.kill()  # cuts mid-word, amp:0, state:attend

    # ── visitor sessions ─────────────────────────────────────────────────────

    def _start_new_session(self) -> None:
        """PTT tap (hearing.py's _cancel_as_tap) — new visitor. Ends whatever
        was happening, resets brain history + voice gender lock, then asks
        for a name before resuming normal conversation."""
        log.info("new session — PTT tap")
        if self._speak_task and not self._speak_task.done():
            self._interrupt()
        self.brain.reset()
        self.voice.reset_gender()
        self._awaiting_name = True
        self._name_attempts = 0
        self._speak_task = asyncio.create_task(self._say_line(config.SESSION_NAME_PROMPT))

    async def _handle_name_reply(self, text: str) -> None:
        # Check the full raw transcript against the roster BEFORE the generic
        # <=2-word name extractor runs — VIPs are often introduced with a
        # title attached ("I'm the Chief Minister, Sohail Afridi"), which
        # _extract_name would otherwise mangle into just "Chief Minister".
        guest = self.guests.match_in_text(text)
        if guest:
            self._awaiting_name = False
            # VIP match — personalized LLM welcome instead of the generic ack
            await self._speak(self.brain.welcome_special_guest(guest))
            return

        name = _extract_name(text)
        if name:
            self._awaiting_name = False
            self.brain.set_visitor_name(name)
            await self._say_line(config.SESSION_NAME_ACK.format(name=name))
        elif self._name_attempts == 0:
            self._name_attempts += 1
            await self._say_line(config.SESSION_NAME_RETRY_PROMPT)
        else:
            # two unclear tries — stop asking and answer what they actually
            # said instead, rather than leaving them met with silence
            self._awaiting_name = False
            await self._turn(text)

    async def _speak(self, tokens: AsyncIterator[str]) -> None:
        """SPEAK → RESET, shared by _turn/_say_line/guest welcome -- same
        state/interrupt handling regardless of where the token stream
        (LLM-generated or a fixed line) comes from. Only idle/speak exist
        now -- voice.speak_stream() publishes "speak" itself, immediately."""
        self._interrupted = False
        try:
            self.hearing.robot_speaking = True  # duck VAD, arm barge-in
            await self.voice.speak_stream(tokens)
        finally:
            self.hearing.robot_speaking = False
        if not self._interrupted:
            self.robot.state("idle")  # RESET — barge-in already set idle instead

    async def _say_line(self, text: str) -> None:
        """A fixed, non-LLM-generated line (session prompts)."""
        await self._speak(self._say(text))

    # ── one conversation turn ────────────────────────────────────────────────

    async def _turn(self, text: str) -> None:
        self._t_final = time.perf_counter()
        self._t_first_token = None
        self._t_first_audio = None

        # THINK — filler is brain's first yield, so it plays NOW
        await self._speak(self._tap(self.brain.respond(text)))

        if self.latency:
            self._report_latency(time.perf_counter())

    async def _tap(self, tokens: AsyncIterator[str]) -> AsyncIterator[str]:
        first = True
        async for tok in tokens:
            if first:
                first = False  # filler — not the LLM
            elif self._t_first_token is None:
                self._t_first_token = time.perf_counter()
            yield tok

    def _report_latency(self, t_done: float) -> None:
        def ms(t: float | None) -> str:
            return f"{(t - self._t_final) * 1000:6.0f}ms" if t else "     —"

        # hearing finalizes the moment the silence window closes, so
        # speech_end ≈ final - VAD_SILENCE_MS by construction
        vad_ms = config.VAD_SILENCE_MS
        first_audio = self._t_first_audio
        total = (vad_ms + (first_audio - self._t_final) * 1000) if first_audio else None
        print(
            f"  latency: speech_end→final {vad_ms}ms (VAD window)"
            f" | final→first_token {ms(self._t_first_token)}"
            f" | final→first_audio {ms(first_audio)}"
            f" | final→done {ms(t_done)}\n"
            f"  speech_end→first_audio: {total:.0f}ms (target <1000)"
            if total is not None else
            f"  latency: no audio played (aborted turn)"
        )


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Rebotic — talk to it")
    parser.add_argument("--latency", action="store_true", help="print per-turn timing breakdown")
    parser.add_argument("--debug", action="store_true", help="log partials and debug info")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname).1s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    print("booting — loading models (~10s)...")
    app = Rebotic(latency=args.latency)
    print(f"Rebotic up — mode={config.INPUT_MODE}, backend={config.LLM_BACKEND}. Ctrl-C to quit.")
    await app.run()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\ninterrupted during boot")  # after boot, SIGINT shuts down gracefully

