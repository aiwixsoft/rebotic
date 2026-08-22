"""Spec 01 — HEARING. Mic → Silero VAD → Vosk → events on the bus.

Emits speech_start / partial / final / barge_in. Never calls the brain.
The sounddevice callback runs on its own thread and hands frames to the
event loop via call_soon_threadsafe; all VAD/Vosk work happens on the loop.
"""

import asyncio
import io
import json
import logging
import time
import wave
from collections import deque

import numpy as np
import sounddevice as sd
from groq import AsyncGroq

import bus as events
from bus import Bus
from nethealth import health

log = logging.getLogger("hearing")


class Hearing:
    def __init__(self, bus: Bus, config) -> None:
        self.bus = bus
        self.cfg = config
        # main.py sets this during TTS playback: ducks VAD to the playback
        # threshold and turns speech_start into barge_in
        self._robot_speaking = False
        self._robot_speaking_since = 0.0
        self._quiet_until = 0.0  # reverb tail after our own reply ends

        import torch
        from silero_vad import load_silero_vad
        self._torch = torch
        self._vad = load_silero_vad(onnx=True)  # ~1ms/frame on CPU

        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        self._rec = KaldiRecognizer(Model(config.VOSK_MODEL_PATH), config.SAMPLE_RATE)  # once (~2s)

        # Online STT tier: Vosk's final transcript is the instant, always-available
        # answer; Groq Whisper — sent in parallel with the raw turn audio — replaces
        # it when it lands in time. Empty model or no keys = tier stays off, Vosk-only.
        # One client per key (GROQ_API_KEY/_2/_3/...) — separate quota buckets,
        # rotated in _transcribe_online on failure, same idea as brain.py's LLM cascade.
        self._stt_online = bool(getattr(config, "STT_ONLINE_MODEL", "")) and bool(config.GROQ_API_KEYS)
        self._stt_clients = [
            AsyncGroq(api_key=key, timeout=config.STT_ONLINE_TIMEOUT_S, max_retries=0)
            for key in config.GROQ_API_KEYS
        ] if self._stt_online else []
        max_audio_frames = int(config.STT_ONLINE_MAX_S * config.SAMPLE_RATE / config.FRAME_SIZE)
        self._audio_frames: deque = deque(maxlen=max_audio_frames)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: sd.InputStream | None = None
        self._listener = None
        self._running = False
        # bounded — drop frames under load instead of growing for 6 hours
        self._frames: asyncio.Queue = asyncio.Queue(maxsize=256)

        # VAD state machine
        self._speaking = False
        self._ptt_down = False
        # Visitor sessions: a quick PTT tap (not a held word) signals "new
        # visitor". Only meaningful when THIS press is what started speaking —
        # a PTT release during an already-VAD-active utterance is never a tap.
        self._ptt_press_t = 0.0
        self._ptt_initiated_speaking = False
        self._on_count = 0
        self._silence_ms = 0.0
        self._speech_ms = 0.0
        self._finalized: list[str] = []
        self._last_partial = ""
        # ~256ms of audio from before the VAD trigger so the first word isn't clipped
        self._preroll: deque = deque(maxlen=8)
        # raw audio of the current turn, for pitch → gender (bounded)
        max_pitch_frames = int(config.PITCH_MAX_ANALYSIS_S * config.SAMPLE_RATE / config.FRAME_SIZE)
        self._pitch_frames: deque = deque(maxlen=max_pitch_frames)

    @property
    def robot_speaking(self) -> bool:
        return self._robot_speaking

    @robot_speaking.setter
    def robot_speaking(self, value: bool) -> None:
        if value and not self._robot_speaking:
            self._robot_speaking_since = time.monotonic()
        elif self._robot_speaking and not value:
            self._quiet_until = time.monotonic() + self.cfg.BARGE_IN_SUPPRESS_MS / 1000
        self._robot_speaking = value

    # ── mic callback (sounddevice thread — touch nothing but the handoff) ────

    def _on_audio(self, indata: np.ndarray, _frames, _time, status) -> None:
        if status:
            log.warning("audio status: %s", status)
        if self._loop is not None and self._running:
            self._loop.call_soon_threadsafe(self._enqueue, indata.copy())

    def _enqueue(self, frame: np.ndarray) -> None:
        try:
            self._frames.put_nowait(frame)
        except asyncio.QueueFull:
            pass  # drop — never buffer unboundedly

    # ── interface ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Opens mic, runs forever (until stop())."""
        self._loop = asyncio.get_running_loop()
        self._running = True
        self._start_ptt()
        self._stream = sd.InputStream(
            samplerate=self.cfg.SAMPLE_RATE,  # capture at 16k — never resample later
            blocksize=self.cfg.FRAME_SIZE,    # 512 — Silero requires exactly this
            channels=1,
            dtype="int16",
            callback=self._on_audio,
        )
        self._stream.start()
        while self._running:
            frame = await self._frames.get()
            if frame is None:
                break
            self._process(frame.reshape(-1))

    async def stop(self) -> None:
        self._running = False
        self._frames.put_nowait(None)  # unblock the consumer
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        if self._listener is not None:
            self._listener.stop()

    # ── push-to-talk (PRIMARY at expo — VAD misfires in a loud hall) ─────────

    def _start_ptt(self) -> None:
        if self.cfg.INPUT_MODE not in ("ptt", "both"):
            return
        try:
            from pynput import keyboard
        except Exception as e:
            log.warning("PTT unavailable (%s) — falling back to VAD only", e)
            return

        def on_press(key):
            if key == keyboard.Key.space:
                self._loop.call_soon_threadsafe(self._set_ptt, True)

        def on_release(key):
            if key == keyboard.Key.space:
                self._loop.call_soon_threadsafe(self._set_ptt, False)

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()

    def _set_ptt(self, down: bool) -> None:
        if down == self._ptt_down:
            return  # key auto-repeat
        self._ptt_down = down
        if down and not self._speaking:
            self._ptt_press_t = time.monotonic()
            self._ptt_initiated_speaking = True
            self._enter_speaking()      # button down → force speech_start, bypass VAD
        elif not down and self._speaking:
            held_ms = (time.monotonic() - self._ptt_press_t) * 1000
            if self._ptt_initiated_speaking and held_ms < self.cfg.PTT_TAP_MAX_MS:
                self._cancel_as_tap()   # quick tap, not a spoken turn — new-session signal
            else:
                self._finalize()        # button up → end-of-turn immediately
            self._ptt_initiated_speaking = False

    # ── VAD state machine ────────────────────────────────────────────────────

    def _vad_prob(self, frame: np.ndarray) -> float | None:
        if self.cfg.INPUT_MODE == "ptt" or self._ptt_down:
            return None  # PTT overrides VAD entirely while active
        f32 = frame.astype(np.float32) / 32768.0
        return self._vad(self._torch.from_numpy(f32), self.cfg.SAMPLE_RATE).item()

    def _process(self, frame: np.ndarray) -> None:
        cfg = self.cfg
        frame_ms = 1000.0 * len(frame) / cfg.SAMPLE_RATE
        prob = self._vad_prob(frame)

        if not self._speaking:
            self._preroll.append(frame)
            if prob is None:
                return
            # duck the threshold while the bot is talking — mic stays live,
            # but only confident speech (not our own speaker echo) barges in
            if self.robot_speaking:
                suppressed = (
                    not cfg.VAD_BARGE_IN  # open-mic setup: never trust VAD over our own audio
                    or (time.monotonic() - self._robot_speaking_since) * 1000 < cfg.BARGE_IN_SUPPRESS_MS
                )
                if suppressed:
                    self._on_count = 0
                    return
                threshold = cfg.VAD_THRESHOLD_PLAYBACK
            else:
                if time.monotonic() < self._quiet_until:
                    self._on_count = 0  # our reply's reverb tail, not a visitor
                    return
                threshold = cfg.VAD_THRESHOLD_ON
            self._on_count = self._on_count + 1 if prob > threshold else 0
            if self._on_count >= 2:
                self._enter_speaking()
            return

        # SPEAKING: stream into Vosk, watch for end-of-turn
        self._pitch_frames.append(frame)
        if self._stt_online:
            self._audio_frames.append(frame)
        self._feed_vosk(frame)
        self._speech_ms += frame_ms
        if prob is None:
            self._silence_ms = 0.0  # PTT held — turn ends on release, not silence
            return
        # hysteresis: leaving speech uses a lower bar than entering it
        if prob < cfg.VAD_THRESHOLD_OFF:
            self._silence_ms += frame_ms
        else:
            self._silence_ms = 0.0
        if self._silence_ms >= self._required_silence_ms():
            self._finalize()

    def _required_silence_ms(self) -> float:
        """Human turn-taking: a pause after "what is the..." is a thought,
        not an ending — hold the longer window when the last word heard
        can't close an English sentence."""
        tail = self._last_partial or (self._finalized[-1] if self._finalized else "")
        last_word = tail.rsplit(maxsplit=1)[-1] if tail.strip() else ""
        if last_word in self.cfg.INCOMPLETE_TAIL_WORDS:
            return self.cfg.VAD_SILENCE_HOLD_MS
        return self.cfg.VAD_SILENCE_MS

    def _enter_speaking(self) -> None:
        self._speaking = True
        self._on_count = 0
        self._silence_ms = 0.0
        self._speech_ms = 0.0
        self._finalized.clear()
        self._last_partial = ""
        self._pitch_frames.clear()
        self._audio_frames.clear()
        for f in self._preroll:  # catch the onset the trigger delay missed
            self._pitch_frames.append(f)
            if self._stt_online:
                self._audio_frames.append(f)
            self._feed_vosk(f)
        self._preroll.clear()
        # fires IMMEDIATELY — this is what makes the head turn feel reactive
        self.bus.publish(events.speech_start())
        if self.robot_speaking:
            self.bus.publish(events.barge_in())

    def _feed_vosk(self, frame: np.ndarray) -> None:
        if self._rec.AcceptWaveform(frame.tobytes()):
            text = json.loads(self._rec.Result()).get("text", "")
            if text:
                self._finalized.append(text)
                self._last_partial = ""  # stale — newest words live in _finalized now
        else:
            p = json.loads(self._rec.PartialResult()).get("partial", "")
            if p and p != self._last_partial:
                self._last_partial = p
                self.bus.publish(events.partial(" ".join([*self._finalized, p]).strip()))

    def _estimate_gender(self) -> str:
        """Median F0 of the turn's voiced audio → male/female/unknown.
        Per-window YIN (cumulative mean normalized difference) — unlike raw
        autocorrelation it doesn't octave-jump onto harmonics. Windows without
        clear voicing (silence, fricatives, noise) don't vote."""
        cfg = self.cfg
        if not self._pitch_frames:
            return "unknown"
        audio = np.concatenate(list(self._pitch_frames))
        lo = int(cfg.SAMPLE_RATE / cfg.PITCH_MAX_HZ)  # shortest period searched
        hi = int(cfg.SAMPLE_RATE / cfg.PITCH_MIN_HZ)  # longest
        win = 1024 + hi  # correlate a 1024-sample base against lags up to hi
        f0s = []
        for i in range(0, len(audio) - win, 1024):
            w = audio[i:i + win].astype(np.float32)
            w -= w.mean()
            if np.sqrt(np.mean(w ** 2)) < 300:  # int16 scale — too quiet to trust
                continue
            # YIN difference d(τ) = Σ (x[j] - x[j+τ])² over a fixed-length base
            shifted = np.lib.stride_tricks.sliding_window_view(w, win - hi)  # (hi+1, base)
            d = ((shifted - shifted[0]) ** 2).sum(axis=1)
            tau = np.arange(1, hi + 1, dtype=np.float32)
            dprime = d[1:] * tau / np.maximum(np.cumsum(d[1:]), 1e-9)
            band = dprime[lo - 1:]  # lags lo..hi
            if float(band.min()) > 0.35:
                continue  # no clear voicing in this window
            below = np.nonzero(band < 0.15)[0]
            if len(below):
                j = int(below[0])  # smallest qualifying lag = true period
                while j + 1 < len(band) and band[j + 1] < band[j]:
                    j += 1  # slide down to the local minimum
            else:
                j = int(np.argmin(band))
            lag = lo + j
            # harmonic guard: a strong 2nd harmonic dips at half the true
            # period — if double this period dips even lower, that's the pitch
            twice = 2 * lag
            if twice <= hi:
                a = max(int(twice * 0.9), lo)
                b = min(int(twice * 1.1) + 1, hi)
                k = int(np.argmin(dprime[a - 1:b])) + a
                if dprime[k - 1] < min(0.15, dprime[lag - 1] * 0.95):
                    lag = k
            f0s.append(cfg.SAMPLE_RATE / lag)
        if len(f0s) < cfg.PITCH_MIN_VOICED_WINDOWS:
            return "unknown"
        # low quartile, not median — the errors above only ever vote too HIGH
        return "male" if float(np.percentile(f0s, 25)) < cfg.PITCH_MALE_MAX_HZ else "female"

    def _finalize(self) -> None:
        cfg = self.cfg
        self._speaking = False
        tail = json.loads(self._rec.FinalResult()).get("text", "")
        self._rec.Reset()  # missing this leaks state across turns
        self._vad.reset_states()
        text = " ".join([*self._finalized, tail]).strip()
        speech_ms = self._speech_ms - self._silence_ms
        gender = self._estimate_gender()
        audio = (np.concatenate(list(self._audio_frames)).tobytes()
                 if self._stt_online and self._audio_frames else None)
        self._pitch_frames.clear()
        self._audio_frames.clear()
        self._finalized.clear()
        self._last_partial = ""
        self._silence_ms = 0.0

        # junk filter — every rejected transcript is a Groq request saved
        if len(text) < cfg.MIN_TRANSCRIPT_CHARS:
            return
        if speech_ms < cfg.VAD_MIN_SPEECH_MS:
            return
        if text in cfg.JUNK_SET:
            return
        if len(text) > cfg.MAX_TRANSCRIPT_CHARS:
            text = text[:cfg.MAX_TRANSCRIPT_CHARS]

        if audio is not None and health.ok:
            asyncio.create_task(self._publish_online(text, gender, audio))
        else:
            self.bus.publish(events.final(text, gender))

    def _cancel_as_tap(self) -> None:
        """A quick PTT tap (<PTT_TAP_MAX_MS, see _set_ptt) signals 'new
        visitor session' rather than a spoken turn — same recognizer/VAD/
        buffer reset as _finalize, but skips the transcript entirely and
        publishes new_session instead of final."""
        self._speaking = False
        self._rec.Reset()
        self._vad.reset_states()
        self._pitch_frames.clear()
        self._audio_frames.clear()
        self._finalized.clear()
        self._last_partial = ""
        self._silence_ms = 0.0
        self.bus.publish(events.new_session())

    async def _publish_online(self, fallback_text: str, gender: str, pcm: bytes) -> None:
        """Vosk's transcript already shipped as `fallback_text` — try to beat it
        with Groq Whisper before the event goes out. A miss costs nothing but
        the timeout; the turn always gets a transcript, never silence."""
        text = fallback_text
        try:
            online_text = await asyncio.wait_for(
                self._transcribe_online(pcm), self.cfg.STT_ONLINE_TIMEOUT_S)
            if online_text.strip():
                text = online_text.strip()
        except Exception as e:
            log.warning("online STT failed: %r — using local transcript", e)
            health.mark_down()
        if len(text) > self.cfg.MAX_TRANSCRIPT_CHARS:
            text = text[:self.cfg.MAX_TRANSCRIPT_CHARS]
        self.bus.publish(events.final(text, gender))

    async def _transcribe_online(self, pcm: bytes) -> str:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)  # int16
            w.setframerate(self.cfg.SAMPLE_RATE)
            w.writeframes(pcm)
        wav_bytes = buf.getvalue()
        last_err: Exception = RuntimeError("no Groq keys configured for online STT")
        for client in self._stt_clients:
            try:
                resp = await client.audio.transcriptions.create(
                    file=("turn.wav", wav_bytes),
                    model=self.cfg.STT_ONLINE_MODEL,
                    language=getattr(self.cfg, "STT_ONLINE_LANGUAGE", "en"),
                )
                return resp.text
            except Exception as e:
                last_err = e
                log.warning("groq whisper key failed: %r — trying next key", e)
        raise last_err


# ── standalone test ───────────────────────────────────────────────────────────

async def _main() -> None:
    import config

    logging.basicConfig(level=logging.INFO, format="%(levelname).1s %(name)s: %(message)s")
    bus = Bus()
    hearing = Hearing(bus, config)
    print(f"mode={config.INPUT_MODE} — speak, or hold SPACE for push-to-talk. Ctrl-C quits.")

    task = asyncio.create_task(hearing.start())
    t0 = time.monotonic()
    try:
        while True:
            ev = await bus.get()
            data = f"  {ev.data}" if ev.data else ""
            print(f"[{(time.monotonic() - t0) * 1000:8.0f}ms] {ev.type}{data}")
    finally:
        await hearing.stop()
        task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nbye")
