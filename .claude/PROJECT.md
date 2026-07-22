# Rebotic — Human-Like Conversational Robot

Real-time voice robot. Laptop = brain, ESP32 = body.
Target: **< 1s** from end-of-speech to first audible word.

---

## Hardware Reality

| | |
|---|---|
| Controller | Laptop — i5 8th gen, 16GB RAM, **CPU only, no CUDA** |
| Body | ESP32 + servos + LEDs |
| Transport | MQTT over local WiFi (Mosquitto on laptop) |
| Deadline context | 6-hour continuous expo demo |

**No GPU** is the defining constraint. It rules out local Whisper and any local LLM above ~1.5B for realtime use.

---

## Stack (all free)

| Part | Tool | Where | Latency |
|---|---|---|---|
| VAD | Silero VAD | local | ~1ms |
| STT | Vosk (`small-en-us-0.15`) | local | ~200-400ms |
| LLM | Groq `llama-3.3-70b-versatile` | cloud | ~100ms TTFT |
| LLM fallback | Groq `llama-3.1-8b-instant` | cloud | ~80ms TTFT |
| LLM offline | Ollama `qwen2.5:1.5b-instruct` | local | ~600ms TTFT |
| TTS | Piper (`en_US-amy-medium`) | local | ~50ms |

Budget: **~700ms–1s** round trip.

---

## Architecture

```
Mic → [1 HEARING] → transcript → [2 BRAIN] → tokens → [3 VOICE] → Speaker
           |                                              |
           |                                              └→ amp → MQTT → ESP32
           └──────── barge-in: abort 2 and 3 ─────────────┘
```

Three independent modules on one async event bus. Each testable in isolation.

- [`specs/01-hearing.md`](specs/01-hearing.md) — audio in → transcript + turn events
- [`specs/02-brain.md`](specs/02-brain.md) — transcript → streamed response tokens
- [`specs/03-voice.md`](specs/03-voice.md) — tokens → audio + mouth-sync amplitude

Hardware is a separate subproject: [`../body/PROJECT.md`](../body/PROJECT.md) (body-mounted USB audio, ESP32 motion, wiring, bring-up).

---

## Conversation Flow

```
1. IDLE        ESP32 blinks/sways. Mic open, VAD listening.
2. DETECT      VAD speech_start → MQTT {state:"attend"} → head turns
3. TRANSCRIBE  Audio streams to Vosk, partials arrive live
4. END TURN    VAD silence 400ms → finalize transcript
5. THINK       MQTT {state:"thinking"} → filler audio plays NOW
6. GENERATE    Groq streams tokens
7. SPEAK       First clause → Piper → audio; RMS → MQTT {amp:0-255} @40Hz
8. INTERRUPT   VAD fires any time in 6-7 → kill TTS, abort LLM, goto 2
9. RESET       MQTT {state:"idle"} → goto 1
```

**Key trick:** step 5 plays a canned filler ("hmm", "let's see") the instant speech ends. Masks ~800ms of LLM latency. Costs nothing, buys everything.

---

## Build Order

**3 → 2 → 1.** Voice first. Bad TTS ruins the project regardless of everything else — you'll know in 10 minutes if it's usable.

---

## MQTT Contract

Laptop sends **intent**, never raw servo angles. Network jitter makes direct angles twitch. ESP32 owns all timing and interpolation.

```json
{"t":"state","v":"idle|attend|thinking|speak"}
{"t":"amp","v":0-255}              // 40Hz during speech only
{"t":"gest","v":"nod|tilt|shrug"}
```

Topic: `rebotic/cmd` · Broker: `localhost:1883` · QoS 0

---

## Expo Survival

### Never go silent — 3-layer cascade
```
Groq 70B  ──429/timeout──→  Groq 8B  ──fail──→  Ollama 1.5b  ──fail──→  canned
   ↑                            ↑
 2s timeout              separate quota bucket
```

### Rules
- **Phone hotspot**, not venue WiFi. Venue WiFi is hostile.
- **Two Groq keys** (two Google accounts). Rotate on 429. RPD is the wall, not RPM.
- **Push-to-talk is primary**, not backup. 6h of expo noise destroys VAD.
- **Wired headset mic.** Laptop mic is unusable in a crowd.
- **Servos: `detach()` when idle.** 6h of holding current cooks SG90s.
- **External 5V for servos**, common ground. ESP32 regulator will brown out.
- **Trim history to 4 turns.** Unbounded = RAM climb + slower every hour.
- **Reset state after 60s idle.** One visitor's weird chat shouldn't confuse the next.
- **Watchdog** on Python process + ESP32 hardware WDT. Both will hang in 6h.
- **Laptop on a stand, plugged into mains.** Battery mode downclocks. Throttling at hour 3 kills your latency.
- **Constrain the persona.** Open-domain = it says something dumb in front of judges.

### Ship list
```
□ Power bank + mains adapter    □ 2nd Groq key
□ Spare servos ×2               □ Push-to-talk button
□ Spare jumpers                 □ Wired headset mic
□ Laptop stand                  □ Watchdog script tested
```

---

## Layout

```
rebotic/
├── PROJECT.md
├── specs/
│   ├── 01-hearing.md
│   ├── 02-brain.md
│   └── 03-voice.md
├── CLAUDE_CODE_PROMPT.md
├── .env                    # GROQ_API_KEY, GROQ_API_KEY_2
├── config.py               # all tunables, one file
├── bus.py                  # asyncio.Queue event bus
├── hearing.py
├── brain.py
├── voice.py
├── robot.py                # MQTT publisher
├── main.py                 # wires the three parts
└── models/
    ├── vosk-model-small-en-us-0.15/
    └── en_US-amy-medium.onnx
```

---

## Setup

```bash
pip install silero-vad vosk piper-tts sounddevice groq paho-mqtt numpy python-dotenv

# Vosk model (~40MB)
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip -d models/

# Piper voice
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx -P models/
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json -P models/

# Offline LLM fallback
ollama pull qwen2.5:1.5b-instruct

# Broker
sudo apt install mosquitto && sudo systemctl start mosquitto
```

**Groq key:** console.groq.com → Google sign-in → API Keys → Create → copy immediately (shown once) → `.env`.
Then Settings → Limits. **Screenshot your actual RPD.** That's your expo budget — not a guess.

---

## Do Not

- ❌ Build on `qwen2:0.5b` or `tinyllama`. They cannot hold a conversation. You'll debug a pipeline that's fine.
- ❌ Run the LLM without `max_tokens`. Long replies = dead air = broken illusion.
- ❌ Wait for full LLM response before TTS. Stream or die.
- ❌ Send servo angles over MQTT.
- ❌ Call the LLM on empty/garbage transcripts. Burns quota.
- ❌ Use PyAudio. Use `sounddevice`.
