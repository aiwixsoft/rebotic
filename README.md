# Rebotic

A voice-driven robot assistant: mic → VAD → speech-to-text → LLM → text-to-speech → speaker,
with an optional MQTT link to a physical robot body (ESP32 — motion, eyes, push-to-talk).

Built for a tech-expo demo: it has to keep talking even when the venue wifi drops, so
every layer that touches the network has a local fallback.

```
mic → Silero VAD → Vosk (+ Groq Whisper online)         hearing.py
        │ transcript
        ▼
LLM cascade: Groq 70B → Groq 8B → Ollama (local) → canned answers   brain.py
        │ streamed reply
        ▼
TTS: Microsoft neural voices (online) → Piper (offline, ONNX)       voice.py
        │ PCM
        ▼
speaker  +  MQTT → robot body (state / mouth amplitude / gestures)  robot.py
```

A shared circuit breaker (`nethealth.py`) means one network failure switches the LLM
*and* the voice to their offline tiers together, and both recover automatically the
moment the connection comes back — mid-conversation, with no lost context.

---

## 0. What you need before starting

- **Linux** (built and tested on Ubuntu 24.04) **or Windows 10/11**. The Linux steps
  below were run start-to-finish on real hardware during development; the Windows steps
  use each tool's standard official installer/instructions but weren't run against this
  exact machine — if something's off, it's likely a version-specific installer quirk,
  not the underlying approach.
- Python 3.12
- A microphone and speaker (built-in laptop audio is fine)
- A free [Groq](https://console.groq.com) API key (the cloud LLM/Whisper tier — the app
  still works without one, see step 6)
- ~250MB disk for models

Every step below has a Linux and a Windows version — follow the one for your OS. Steps
that don't differ by OS aren't split.

---

## 1. Clone the repo

```bash
git clone git@github.com:aiwixsoft/rebotic.git
cd rebotic
```

## 2. System packages

#### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y python3-venv portaudio19-dev ffmpeg espeak-ng mosquitto-clients
```

| package | why |
|---|---|
| `portaudio19-dev` | native lib `sounddevice` needs for mic/speaker I/O |
| `ffmpeg` | decodes the online TTS voices' mp3 output to raw PCM |
| `espeak-ng` | Piper's phonemizer backend (offline TTS) |
| `mosquitto-clients` | only needed if you want to test the MQTT robot-body link (see step 8) |

#### Windows

1. **Python 3.12** — install from [python.org](https://www.python.org/downloads/) and
   make sure you tick **"Add python.exe to PATH"** in the installer.
2. **ffmpeg** (decodes the online TTS mp3 output to PCM) — easiest via
   [winget](https://learn.microsoft.com/windows/package-manager/winget/):
   ```powershell
   winget install ffmpeg
   ```
   (no winget? grab a build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add
   its `bin` folder to your PATH manually)
3. **PortAudio** and **espeak-ng** — nothing to install separately. The `sounddevice` and
   `piper-tts`/`piper-phonemize` wheels for Windows bundle these natively. If Piper still
   throws a phonemization error later, install
   [espeak-ng for Windows](https://github.com/espeak-ng/espeak-ng/releases) as a fallback.
4. **Mosquitto** — only if you have the physical robot body; see step 8.

## 3. Python environment + dependencies

#### Linux

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

#### Windows

```powershell
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
```

## 4. Install PyTorch (CPU build, separately)

`silero-vad` needs `torch`/`torchaudio`. **Do not just `pip install torch`** — the
default PyPI wheel pulls in a CUDA build that needs an NVIDIA GPU and will fail to
import (`libcudart.so` / `.dll` errors) on a CPU-only machine. Install the CPU build
explicitly instead. This command is identical on both OSes (just via each OS's `pip`):

#### Linux
```bash
.venv/bin/pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
```

#### Windows
```powershell
.venv\Scripts\pip.exe install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
```

## 5. Download the models

#### Linux

```bash
mkdir -p models && cd models

# Piper offline TTS voices (female + male, ~60MB each)
wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json

# Vosk offline speech recognizer (~40MB)
wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip -q vosk-model-small-en-us-0.15.zip
rm vosk-model-small-en-us-0.15.zip

cd ..
```

#### Windows

Use `curl.exe` explicitly (plain `curl` in PowerShell is aliased to `Invoke-WebRequest`,
which takes different flags):

```powershell
mkdir models
cd models

# Piper offline TTS voices (female + male, ~60MB each)
curl.exe -sL -o en_US-amy-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
curl.exe -sL -o en_US-amy-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
curl.exe -sL -o en_US-ryan-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
curl.exe -sL -o en_US-ryan-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json

# Vosk offline speech recognizer (~40MB)
curl.exe -sL -o vosk-model-small-en-us-0.15.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
Expand-Archive vosk-model-small-en-us-0.15.zip -DestinationPath .
Remove-Item vosk-model-small-en-us-0.15.zip

cd ..
```

Either way you should end up with `models/en_US-amy-medium.onnx(.json)`,
`models/en_US-ryan-medium.onnx(.json)`, and `models/vosk-model-small-en-us-0.15/`.

## 6. Groq API key (cloud LLM + cloud speech recognition)

```bash
cp .env.example .env          # Windows PowerShell/cmd: copy .env.example .env
```

Get a free key at [console.groq.com](https://console.groq.com) → API Keys → Create, then
edit `.env`:

```
GROQ_API_KEY=gsk_...
GROQ_API_KEY_2=          # optional — a second key doubles your quota, rotated on 429s
```

**No Groq key?** The app still runs — `LLM_BACKEND` falls back through Ollama (step 7)
down to scripted canned answers, and speech recognition falls back to the offline Vosk
model automatically. You just lose the cloud tiers' accuracy/quality.

## 7. Ollama (offline LLM fallback — recommended)

This is what answers when the internet is down or slow.

#### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:0.5b-instruct
```

#### Windows

Download and run the installer from [ollama.com/download](https://ollama.com/download)
(GUI installer, adds `ollama` to PATH). Then, in a **new** terminal:

```powershell
ollama pull qwen2.5:0.5b-instruct
```

(A bigger model like `qwen2.5:1.5b-instruct` answers better but is noticeably slower on
CPU — the 0.5b model is tuned for first-token latency at a demo, not eloquence. You can
swap `config.OLLAMA_MODEL` if your machine is fast enough to afford it.)

## 8. Mosquitto (only if you have the physical robot body)

Purely optional — `robot.py` fails silently with no broker running, and the voice
conversation works fine standalone. Only set this up if you're wiring up the ESP32 body
described in `body/`:

#### Linux

```bash
sudo apt install -y mosquitto
sudo systemctl enable --now mosquitto
```

#### Windows

Download the installer from [mosquitto.org/download](https://mosquitto.org/download/)
(Windows section), run it, and enable "Service" during setup so it starts automatically.

See `body/PROJECT.md` for the hardware side (separate subproject, its own specs).

## 9. Run it

#### Linux

```bash
.venv/bin/python main.py
```

#### Windows

```powershell
.venv\Scripts\python.exe main.py
```

You should hear a greeting, then `listening — speak, or hold SPACE for push-to-talk`.
Talk normally, or hold spacebar to push-to-talk (useful in a loud room where VAD
misfires). Ctrl-C to quit.

Useful flags (same on both OSes, just swap the python path above):

```bash
main.py --latency   # print per-turn timing breakdown (speech_end→first_audio etc.)
main.py --debug     # verbose logging: partial transcripts, provider fallbacks
```

Standalone module tests (each runs without the others — use your OS's python path from
step 9 in place of `python`):

```bash
python hearing.py   # mic → transcript, no LLM/TTS
python brain.py     # text REPL against the LLM cascade; /offline and /online simulate a network drop
python voice.py "some text"   # TTS only, plays through the speaker
python robot.py     # MQTT smoke test — watch with `mosquitto_sub -t rebotic/cmd -v`
```

---

## Configuration

Every tunable lives in `config.py`, grouped by module, with the reasoning behind each
value in a comment. A few worth knowing about up front:

- `LLM_BACKEND` — `"groq"` (cloud + offline fallback, default) | `"ollama"` (offline only) | `"canned"`
- `INPUT_MODE` — `"both"` (VAD + push-to-talk, default) | `"vad"` | `"ptt"`
- `TTS_ONLINE_VOICES` — Microsoft neural voices (near-human); empty dict disables the tier
  and speaks pure offline Piper
- `STT_ONLINE_MODEL` — Groq Whisper for speech recognition accuracy; empty string
  disables the tier and uses Vosk only
- `LANGUAGE` — `"en"` (default). There's a dormant `"ur"` (Urdu) mode further down the
  file with its own persona/voices/STT — re-enabling it needs its model files downloaded
  separately (not included in step 5)

## Troubleshooting

- **`libcudart.so` / CUDA DLL error on import** — you installed the GPU build of torch.
  Redo step 4.
- **No sound / speaker silent (Linux)** — check your OS output volume isn't muted or
  near-zero (`wpctl status` / `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.85` on PipeWire
  systems). This has caught out more than one "it's broken" report that was actually just
  the OS volume slider.
- **No sound / speaker silent (Windows)** — check the Volume Mixer (right-click the
  speaker icon in the taskbar) isn't muting Python, and that the correct output device is
  set as default in Sound Settings.
- **Mic not picking anything up (Windows)** — check Settings → Privacy & security →
  Microphone → "Let desktop apps access your microphone" is on.
- **Bot interrupts itself mid-sentence on a laptop's built-in mic** — it's hearing its own
  speaker output. `config.VAD_BARGE_IN = False` (the default) disables VAD barge-in for
  exactly this reason; push-to-talk still interrupts regardless. Only re-enable VAD
  barge-in with a headset or directional mic.
- **First offline (Ollama) reply is very slow** — the CPU has to cold-load the model and
  evaluate the system prompt. `main.py` pre-warms this at boot (`brain.warm_ollama()`),
  so it should only be slow on the very first request right after `ollama pull`.
