# Spec 03 — VOICE

**Module:** `voice.py`
**Job:** Token stream → audible speech, fast. Plus amplitude for mouth-sync.
**Owns:** Playback and mouth sync.

**Build this first.** Bad TTS ruins the project regardless of everything upstream. You'll know in 10 minutes if it's usable.

---

## Pipeline

```
token stream
  → clause chunker      → flush on . ? ! , ; :
  → gesture tag parser  → [nod] → MQTT, strip from speech text
  → Piper               → 22050Hz int16 PCM
  → sounddevice         → speaker
       └→ RMS per frame → MQTT {t:"amp", v:0-255} @ 40Hz
```

---

## Dependencies

```
piper-tts        # models/en_US-amy-medium.onnx + .onnx.json
sounddevice
numpy
paho-mqtt
```

---

## Config

```python
TTS_VOICE      = "models/en_US-amy-medium.onnx"
TTS_RATE       = 22050          # Piper's native — do not resample
CHUNK_ON       = ".!?,;:"
CHUNK_MIN_CHARS = 12            # don't flush "Hi," alone
AMP_HZ         = 40
AMP_SMOOTH     = 0.3            # EMA factor
```

---

## Clause Chunking (this is where the speed comes from)

Buffer tokens. Flush to TTS the moment you hit a boundary:

```python
buf += token
if buf[-1] in CHUNK_ON and len(buf) >= CHUNK_MIN_CHARS:
    yield buf; buf = ""
```

The **first clause** starts speaking while the LLM is still generating the rest. That's the whole sub-1s trick.

**`CHUNK_MIN_CHARS` guards against fragments.** Without it, "Hi," flushes alone and you get choppy stop-start speech. 12 is the starting point.

Flush any remainder at stream end.

---

## Piper

- Load voice **once** at startup. Never per-utterance — that's a ~1s penalty every time.
- Outputs 22050Hz int16 mono.
- ~50ms synthesis on your i5. This is the one component that genuinely flies CPU-only — it's built for Raspberry Pi.
- Voice quality: decent, not Hollywood. Accept it. `en_US-amy-medium` or `en_US-lessac-medium`.

**Do not resample to 16k.** Play at 22050. Only the *mic* side needs 16k.

---

## Amplitude Tap (mouth sync)

Per audio frame, before it hits the speaker:

```python
rms = np.sqrt(np.mean(frame.astype(float)**2))
amp = int(np.clip(rms / 3000 * 255, 0, 255))
amp = int(prev * (1 - AMP_SMOOTH) + amp * AMP_SMOOTH)   # EMA — raw RMS jitters
publish("rebotic/cmd", {"t": "amp", "v": amp})
```

- **40Hz.** Faster floods MQTT for no visible gain; slower looks robotic.
- **EMA smoothing is required.** Raw RMS makes the jaw chatter.
- Publish `{"t":"amp","v":0}` on playback stop, always. Otherwise the jaw freezes open — the single most obviously-broken-looking failure.

---

## Gesture Tags

LLM emits `[nod]` `[tilt]` `[shrug]` inline. Parse them out:

```python
for tag in re.findall(r"\[(\w+)\]", buf):
    publish("rebotic/cmd", {"t": "gest", "v": tag})
buf = re.sub(r"\[\w+\]", "", buf)     # strip BEFORE synthesis
```

Strip before Piper sees it, or it will literally say "bracket nod bracket."

---

## Killable (barge-in)

```python
def kill(self):
    self._abort.set()
    sd.stop()                    # cut audio mid-word
    self.queue.clear()           # drop pending chunks
    publish({"t":"amp","v":0})   # close the jaw
    publish({"t":"state","v":"attend"})
```

Must cut **instantly** — mid-word is correct and sounds natural. A bot that finishes its sentence after you interrupt reads as a machine.

Clearing the pending chunk queue matters: without it, killed audio resumes 200ms later.

---

## Filler Playback

Brain yields a filler ("hmm") immediately on speech_end. Voice speaks it while the real response generates.

**Pre-synthesize all fillers at startup** → cache as raw PCM in a dict. Playing a cached buffer is ~0ms; synthesizing costs you the 50ms you're trying to save.

```python
FILLER_CACHE = {f: piper.synth(f) for f in FILLERS}   # once, at boot
```

---

## Interface

```python
class Voice:
    def __init__(self, bus: asyncio.Queue, mqtt, config): ...
    async def speak_stream(self, tokens: AsyncIterator[str]): ...
    def kill(self): ...
```

---

## Standalone Test

```bash
python voice.py "Hello, I'm Rebotic. [nod] Nice to meet you."
```

**Passes when:**
- Audio starts < 100ms after invocation
- `[nod]` is **not spoken** — published to MQTT instead
- `amp` messages flow at ~40Hz during speech, hit 0 at end
- Ctrl-C cuts audio instantly, mid-word, and jaw closes
- Feed it a slow token generator → speaks first clause before the rest arrives
- 100 consecutive utterances, no RAM growth, no audio device lock-up

That last one matters for 6 hours. Test it.

---

## Failure Modes

| Symptom | Cause |
|---|---|
| Choppy stop-start speech | `CHUNK_MIN_CHARS` too low — fragments flushing |
| Says "bracket nod bracket" | Tags not stripped before synthesis |
| Jaw frozen open | No `amp:0` on stop |
| Jaw chatters | No EMA smoothing on RMS |
| ~1s delay every reply | Voice model reloaded per utterance |
| Killed audio resumes | Pending chunk queue not cleared |
| Chipmunk / slow voice | Resampled away from 22050 |
| Audio dies after hours | Device not released; add watchdog restart |
