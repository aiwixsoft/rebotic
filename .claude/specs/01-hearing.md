# Spec 01 — HEARING

**Module:** `hearing.py`
**Job:** Microphone audio → transcript, and know exactly *when* the user started and stopped talking.
**Owns:** Nothing downstream. Emits events, never calls the brain directly.

---

## Pipeline

```
Mic (16kHz mono PCM, int16)
  → ring buffer (30ms frames)
  → Silero VAD        → speech_start / speech_end
  → Vosk (streaming)  → partial + final transcript
  → event bus
```

---

## Dependencies

```
sounddevice      # mic capture — NOT pyaudio
silero-vad       # torch.hub or pip package
vosk             # models/vosk-model-small-en-us-0.15/
numpy
```

---

## Audio Format (non-negotiable)

| | |
|---|---|
| Rate | 16000 Hz |
| Channels | 1 (mono) |
| Format | int16 PCM |
| Frame | 512 samples (32ms) — Silero requires 512 @ 16kHz |

Both Silero and Vosk want exactly this. Do not resample later — capture at 16k.

---

## VAD Logic

```
SILENT ──speech prob > 0.5 for 2 frames──→ SPEAKING
                                              │
SPEAKING ──speech prob < 0.35 for N frames───┘→ SILENT
```

**Hysteresis matters.** Different thresholds for entering vs leaving speech, or it chatters on the boundary.

### Tunables → `config.py`
```python
VAD_THRESHOLD_ON  = 0.5
VAD_THRESHOLD_OFF = 0.35
VAD_SILENCE_MS    = 400    # end-of-turn. Lower = snappier, more cutoffs
VAD_MIN_SPEECH_MS = 250    # reject coughs/clicks
SAMPLE_RATE       = 16000
FRAME_SIZE        = 512
```

`VAD_SILENCE_MS` is the single most impactful tunable in the project. 400 is the starting point. Below 300 it interrupts people mid-thought; above 600 it feels laggy.

---

## Vosk

- Load model **once** at startup (~2s). Never per-utterance.
- `KaldiRecognizer(model, 16000)`
- Feed frames continuously while VAD says SPEAKING.
- `AcceptWaveform()` returns True → final result available via `Result()`
- Otherwise `PartialResult()` gives live text.
- **`Reset()` the recognizer after every finalized turn.** Missing this leaks state across turns and corrupts later transcripts.

---

## Events Emitted

```python
{"type": "speech_start"}                    # → robot attends immediately
{"type": "partial",  "text": "what is the"} # optional, for debug/UI
{"type": "final",    "text": "what is the weather"}
```

`speech_start` must fire **immediately** on detection — that's what makes the head turn feel reactive. Do not wait for a transcript.

---

## Filtering (expo-critical)

Reject before emitting `final`:

```python
if len(text.strip()) < 2:        return   # noise
if speech_duration_ms < 250:     return   # cough, click, chair scrape
if text in JUNK_SET:             return   # {"the","a","uh","huh",""}
if len(text) > 200:              text = text[:200]   # someone rambling
```

Every rejected junk transcript is a Groq request saved. At 6 hours in a loud room this is the difference between finishing the day and hitting your RPD wall at hour 4.

---

## Push-to-Talk (PRIMARY at expo)

Not a fallback. In a loud hall, VAD misfires constantly — PTT is the main input path.

- Space bar (dev) / GPIO button via ESP32 → MQTT (expo)
- Button down → force `speech_start`, bypass VAD entirely
- Button up → force end-of-turn, finalize immediately
- **Config flag:** `INPUT_MODE = "vad" | "ptt" | "both"`
- `"both"` = PTT overrides VAD when pressed. Ship with `"both"`.

---

## Barge-In

While the bot is speaking, hearing **keeps running**. On `speech_start` during TTS playback:

```
emit {"type": "barge_in"}   →  main loop aborts brain + voice
```

Do not gate the mic during playback. Barge-in is the single biggest realism factor — a bot you can't interrupt reads as a machine.

**Echo risk:** the mic hears your own speaker → false barge-in. Mitigations in order:
1. **Headset mic** (physical separation) — solves it completely, use this at expo
2. Duck VAD threshold to 0.8 during playback
3. Suppress barge-in for the first 300ms of TTS

---

## Interface

```python
class Hearing:
    def __init__(self, bus: asyncio.Queue, config): ...
    async def start(self): ...   # opens mic, runs forever
    async def stop(self): ...    # clean shutdown
```

Runs as its own asyncio task. Push events to `bus`. Never blocks the loop — `sounddevice` callback runs on its own thread, hand frames over with `loop.call_soon_threadsafe`.

---

## Standalone Test

```bash
python hearing.py
```

Prints events as they fire. Speak → see `speech_start`, partials, then `final` ~400ms after you stop.

**Passes when:**
- `speech_start` fires within ~100ms of you starting
- `final` fires ~400ms after you stop, not 2s
- Clapping/coughing produces **no** `final`
- Runs 10 min without RAM growth
- Vosk transcript is recognizable (not perfect — Vosk small is ~85% accurate, that's expected and acceptable)

---

## Failure Modes

| Symptom | Cause |
|---|---|
| Fires on every noise | `VAD_THRESHOLD_ON` too low; use headset mic |
| Cuts user off mid-sentence | `VAD_SILENCE_MS` too low |
| Feels laggy | `VAD_SILENCE_MS` too high |
| Transcripts bleed together | Missing `recognizer.Reset()` |
| Bot interrupts itself | Echo — mic hearing speaker. Use headset. |
| RAM climbs | Model reloaded per utterance, or buffer unbounded |
