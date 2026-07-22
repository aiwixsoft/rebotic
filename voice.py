"""Spec 03 — VOICE. Token stream → clause chunks → TTS → speaker.

Two synthesis tiers, chosen per clause by the shared internet breaker:
  online  — Microsoft neural voices via edge-tts (near-human), mp3 decoded
            to Piper's PCM format through ffmpeg, so downstream is identical
  local   — Piper ONNX voices, always loaded, guaranteed available
A failed/slow online synth falls back to Piper mid-turn and trips the
breaker, so the voice degrades gracefully instead of going silent.

Playback runs through one persistent sounddevice.OutputStream whose callback
pulls PCM from a shared buffer — blocksize is sized so each callback is one
amp frame (22050/40 ≈ 551 samples), giving the 40Hz RMS→MQTT mouth-sync tap
for free. kill() just drains that buffer, so the cut is instant.
"""

import asyncio
import logging
import re
import subprocess
import sys
import threading
import time
from typing import AsyncIterator

import edge_tts
import numpy as np
import sounddevice as sd
from piper import PiperVoice

from bus import Bus
from nethealth import health
from robot import Robot

log = logging.getLogger("voice")

_GESTURE_RE = re.compile(r"\[(\w+)\]")


def _filler_key(text: str) -> str:
    return text.strip().strip(".!?,;:… ").lower()


class Voice:
    def __init__(self, bus: Bus, mqtt: Robot, config) -> None:
        self.bus = bus
        self.mqtt = mqtt
        self.cfg = config

        # Local tier: one Piper model per gender, loaded once — swapped per
        # turn to match the visitor. Always present, works with no internet.
        voices = getattr(config, "TTS_VOICES", None) or {"female": config.TTS_VOICE}
        self._voices = {g: PiperVoice.load(path) for g, path in voices.items()}
        self._gender = getattr(config, "TTS_DEFAULT_GENDER", "female")
        self._gender_locked = False   # first confident detection wins, no flip-flopping mid-conversation
        self._last_gender_set = 0.0

        # Online tier: neural voice names per gender ("" config disables it)
        self._online_voices: dict[str, str] = getattr(config, "TTS_ONLINE_VOICES", {}) or {}
        self._online_fillers: dict[str, dict[str, bytes]] = {g: {} for g in self._online_voices}
        self.last_tier = "local"  # which tier synthesized the last clause (debug/tests)

        self._pcm = bytearray()          # pending int16 samples, shared with audio thread
        self._lock = threading.Lock()
        self._abort = asyncio.Event()
        self._prev_amp = 0

        # Pre-synthesize fillers to raw PCM per voice (also warms up both ONNX
        # sessions, so the first real clause doesn't pay the ~400ms cold-start)
        self._filler_cache: dict[str, dict[str, bytes]] = {
            g: {_filler_key(f): self._synth_with(v, f) for f in config.FILLERS}
            for g, v in self._voices.items()
        }

        # One callback block == one amp frame at AMP_HZ
        self._stream = sd.OutputStream(
            samplerate=config.TTS_RATE,   # Piper native — no resampling
            channels=1,
            dtype="int16",
            blocksize=config.TTS_RATE // config.AMP_HZ,
            callback=self._on_audio_block,
        )
        self._stream.start()

    # ── audio thread ─────────────────────────────────────────────────────────

    def _on_audio_block(self, outdata: np.ndarray, frames: int, _time, _status) -> None:
        want = frames * 2  # int16 bytes
        with self._lock:
            take = self._pcm[:want]
            del self._pcm[:want]
        block = np.frombuffer(bytes(take), dtype=np.int16)
        outdata[: len(block), 0] = block
        outdata[len(block):, 0] = 0

        if len(block):
            rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
            amp = int(np.clip(rms / self.cfg.AMP_RMS_FULL_SCALE * 255, 0, 255))
            amp = int(self._prev_amp * (1 - self.cfg.AMP_SMOOTH) + amp * self.cfg.AMP_SMOOTH)
            self._prev_amp = amp
            self.mqtt.amp(amp)
        elif self._prev_amp:
            self._prev_amp = 0
            self.mqtt.amp(0)  # buffer just ran dry — close the jaw, always

    # ── synthesis ────────────────────────────────────────────────────────────

    def _synth_with(self, voice: PiperVoice, text: str) -> bytes:
        return b"".join(c.audio_int16_bytes for c in voice.synthesize(text))

    def _synth(self, text: str) -> bytes:
        return self._synth_with(self._voices[self._gender], text)

    def _decode_mp3(self, mp3: bytes) -> bytes:
        """edge-tts hands back mp3 at 24kHz; resample to Piper's rate so the
        playback stream and mouth-sync tap never know which tier spoke."""
        out = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-i", "pipe:0",
             "-f", "s16le", "-ar", str(self.cfg.TTS_RATE), "-ac", "1", "pipe:1"],
            input=mp3, capture_output=True,
        ).stdout
        if not out:
            raise RuntimeError("ffmpeg produced no PCM from edge-tts mp3")
        return out

    async def _synth_online_with(self, voice_name: str, text: str) -> bytes:
        mp3 = bytearray()
        async for chunk in edge_tts.Communicate(text, voice_name).stream():
            if chunk["type"] == "audio":
                mp3.extend(chunk["data"])
        if not mp3:
            raise RuntimeError("edge-tts returned no audio")
        return await asyncio.get_running_loop().run_in_executor(
            None, self._decode_mp3, bytes(mp3))

    async def warm_online(self) -> None:
        """Pre-synthesize fillers with the neural voices so an online filler
        is as instant as a local one. Best-effort, fire-and-forget at boot —
        until it lands, online turns borrow the Piper filler."""
        if not self._online_voices or not health.ok:
            return
        try:
            jobs = [(g, _filler_key(f), self._synth_online_with(v, f))
                    for g, v in self._online_voices.items() for f in self.cfg.FILLERS]
            results = await asyncio.gather(*(j for _, _, j in jobs))
            for (g, key, _), pcm in zip(jobs, results):
                self._online_fillers[g][key] = pcm
            log.info("online filler cache ready (%d clips)", len(results))
        except Exception as e:
            log.info("online filler warm-up skipped: %r", e)

    def _enqueue(self, pcm: bytes) -> None:
        with self._lock:
            self._pcm.extend(pcm)

    async def _speak_clause(self, clause: str) -> None:
        for tag in _GESTURE_RE.findall(clause):
            self.mqtt.gest(tag)
        text = _GESTURE_RE.sub("", clause).strip()  # strip BEFORE synthesis
        if not text:
            return
        key = _filler_key(text)
        online = bool(self._online_voices) and health.ok

        # Fillers must be ~0ms — prefer the matching tier's cache, but a
        # not-yet-warmed online filler borrows the Piper clip rather than
        # paying a network round-trip it was meant to mask.
        pcm = (self._online_fillers.get(self._gender, {}).get(key) if online else None) \
            or self._filler_cache[self._gender].get(key)

        if pcm is None and online:
            try:
                pcm = await asyncio.wait_for(
                    self._synth_online_with(self._online_voices[self._gender], text),
                    timeout=self.cfg.TTS_ONLINE_TIMEOUT_S,
                )
                self.last_tier = "online"
            except Exception as e:
                log.warning("online TTS failed: %r — falling back to local voice", e)
                health.mark_down()
        if pcm is None:
            pcm = await asyncio.get_running_loop().run_in_executor(None, self._synth, text)
            self.last_tier = "local"
        if not self._abort.is_set():
            self._enqueue(pcm)

    # ── interface ────────────────────────────────────────────────────────────

    def set_gender(self, gender: str) -> None:
        """Match the visitor: reply male-voiced to men, female-voiced to women —
        but lock onto the FIRST confident detection per conversation. Per-utterance
        pitch estimation is noisy; re-deciding every turn made the voice flip-flop
        mid-conversation. A gap over IDLE_RESET_S means a new visitor (brain resets
        history the same way) and unlocks it. "unknown" guesses never change or
        unlock anything."""
        now = time.monotonic()
        if now - self._last_gender_set > self.cfg.IDLE_RESET_S:
            self._gender_locked = False
        self._last_gender_set = now
        if self._gender_locked or gender not in self._voices:
            return
        self._gender = gender
        self._gender_locked = True

    async def speak_stream(self, tokens: AsyncIterator[str]) -> None:
        """Flush to Piper on clause boundaries; first clause plays while the
        LLM is still generating the rest."""
        self._abort.clear()
        self.mqtt.state("speak")
        buf = ""
        async for token in tokens:
            if self._abort.is_set():
                return
            buf += token
            tail = buf.rstrip()  # LLM tokens carry whitespace — boundary is the last real char
            if not tail:
                continue
            is_boundary = tail[-1] in self.cfg.CHUNK_ON
            is_filler = _filler_key(buf) in self._filler_cache[self._gender]
            # fillers bypass CHUNK_MIN_CHARS — "hmm..." must play instantly
            if is_boundary and (len(tail) >= self.cfg.CHUNK_MIN_CHARS or is_filler):
                await self._speak_clause(buf)
                buf = ""
        if buf.strip() and not self._abort.is_set():
            await self._speak_clause(buf)  # remainder at stream end
        await self._drain()
        self.mqtt.amp(0)  # belt and braces — jaw must never freeze open

    def kill(self) -> None:
        """Barge-in: cut mid-word, drop everything pending."""
        self._abort.set()
        with self._lock:
            self._pcm.clear()  # without this, killed audio resumes a beat later
        self._prev_amp = 0
        self.mqtt.amp(0)
        self.mqtt.state("attend")

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()

    async def _drain(self) -> None:
        while not self._abort.is_set():
            with self._lock:
                pending = len(self._pcm)
            if not pending:
                break
            await asyncio.sleep(0.02)


# ── standalone test ───────────────────────────────────────────────────────────

async def _tokens_at_once(text: str) -> AsyncIterator[str]:
    yield text


async def _tokens_slow(text: str, delay: float = 0.05) -> AsyncIterator[str]:
    """Simulates an LLM stream: one word every 50ms."""
    for word in text.split(" "):
        yield word + " "
        await asyncio.sleep(delay)


async def _main() -> None:
    import config

    args = sys.argv[1:]
    slow = "--slow" in args
    args = [a for a in args if a != "--slow"]
    text = args[0] if args else "Hello, I'm Rebotic. [nod] Nice to meet you."

    t0 = time.perf_counter()
    stamp = lambda label: print(f"[{(time.perf_counter() - t0) * 1000:7.0f}ms] {label}")

    robot = Robot()
    voice = Voice(Bus(), robot, config)
    stamp("boot done (piper loaded, fillers cached, stream open)")

    tokens = _tokens_slow(text) if slow else _tokens_at_once(text)
    if slow:
        n = len(text.split(" "))
        stamp(f"streaming {n} tokens @50ms — last token lands ~{n * 50}ms from now")

    t0 = time.perf_counter()  # timing zero = start of token stream

    orig = voice._speak_clause
    async def traced(clause: str) -> None:
        stamp(f"clause queued for playback: {clause.strip()!r}")
        await orig(clause)
    voice._speak_clause = traced

    try:
        await voice.speak_stream(tokens)
        stamp("playback drained")
    except (KeyboardInterrupt, asyncio.CancelledError):
        voice.kill()
        stamp("killed — audio cut, amp:0, state:attend")
    finally:
        voice.close()
        robot.close()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\ninterrupted")
