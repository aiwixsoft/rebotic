# Rebotic — Full Project Context (for AI assistants: ChatGPT, Gemini, Claude, etc.)

> **Purpose of this document.** This is a single, self-contained brief-in of the entire
> `rebotic` project, written so that another LLM — with no other access to this repo —
> can hold an informed technical conversation about it and generate code that fits its
> real architecture, conventions, and constraints. Paste this whole file into a chat to
> give that assistant full context. It documents both the **software** (Python, on a
> laptop) and the **hardware** (ESP32 robot body) halves, including which hardware parts
> are *specified* vs. *actually built* as of this writing (2026-08-22).
>
> If you're an AI reading this to help write code for this project: match the existing
> style — no unnecessary abstractions, heavy inline comments explaining *why* (not what),
> async/await throughout the Python side, and every design decision here exists because
> of a concrete constraint (CPU-only laptop, 6-hour expo, unreliable venue WiFi). Don't
> "clean up" things that look unusual without understanding why they're that way first —
> the comments in the real source files usually explain it.

---

## 0. What this project is, in one paragraph

**Rebotic** is a real-time, voice-driven conversational robot built for live tech-expo
demos. A laptop (the "mind") runs the full voice pipeline — microphone in, VAD, speech-
to-text, an LLM, text-to-speech, speaker out — and optionally drives a physical robot
body (the "body," an ESP32 board with servos and LED eyes) over MQTT so the robot turns
its head, moves its arms, and looks alive while it talks. The defining constraint is
**CPU-only hardware, no GPU**, so every local ("offline") component is chosen to run
acceptably on a laptop CPU, while the *default*, best-quality path uses free cloud APIs
(Groq for LLM + optional speech recognition, Microsoft's edge-tts for neural voices).
The second defining constraint is **the demo must never go silent**, even if venue WiFi
drops mid-conversation — so every network-dependent layer has a local fallback, and a
shared circuit breaker flips all of them to their offline tier together the instant one
network call fails, then flips back automatically the moment connectivity returns —
without losing conversation history or interrupting the flow.

Target latency: **under 1 second** from the visitor finishing a sentence to the robot's
first audible reply word (masked partly by an instant filler word like "hmm…").

---

## 1. Architecture at a glance

```
 mic → Silero VAD → Vosk (+ optional Groq Whisper online)      hearing.py
         │ transcript
         ▼
 LLM cascade: Groq 70B → Groq 8B → Ollama (local) → canned      brain.py
         │ streamed reply tokens
         ▼
 TTS: Microsoft neural voices (online) → Piper (offline, ONNX)  voice.py
         │ PCM audio
         ▼
 speaker  +  MQTT → robot body (state / mouth amplitude / gestures)  robot.py
```

Three independent Python modules — `hearing.py`, `brain.py`, `voice.py` — never call
each other directly. They communicate only by publishing/consuming typed `Event`
objects on one shared `asyncio.Queue`-based bus (`bus.py`). `main.py` is the only place
that knows about all three; it owns the conversation state machine and wires events to
actions. This means each module is independently testable (`python hearing.py`,
`python brain.py`, `python voice.py "text"` all run standalone).

A fourth always-on concern, `nethealth.py`, is a single shared circuit-breaker object
(`health`) that both `brain.py` (Groq) and `voice.py` (edge-tts) read/write — one network
failure anywhere trips it, both cloud tiers fall back to local together on the *same*
turn, and a background TCP probe (to `api.groq.com:443` every 10s by default) flips it
back automatically.

`robot.py` is a thin, fail-silent MQTT publisher: if no broker is reachable, every
publish is a silent no-op and the voice conversation runs exactly the same — the robot
body is optional, the conversation pipeline is not.

`guests.py` implements a small VIP-recognition feature layered on top of the normal
"ask the visitor's name" flow (see §10).

---

## 2. Repository layout

```
rebotic/
├── main.py              # wires hearing → brain → voice; owns the state machine
├── bus.py                # asyncio.Queue event bus + typed Event constructors
├── config.py              # every tunable, one file, heavily commented with rationale
├── hearing.py             # mic → VAD → STT → transcript events               (spec 01)
├── brain.py               # transcript → streamed LLM reply tokens            (spec 02)
├── voice.py                # token stream → chunked TTS → speaker + mouth-sync (spec 03)
├── robot.py                # MQTT publisher (state/amp/gest) → ESP32 body, fail-silent
├── nethealth.py            # shared internet-health circuit breaker (brain + voice)
├── guests.py               # special-guest/VIP roster loader + fuzzy name matcher
├── special-gest.md         # the VIP roster data file guests.py parses
├── requirements.txt        # pip deps (torch/torchaudio installed separately, CPU build)
├── .env.example             # documents GROQ_API_KEY(_N), TAVILY_API_KEY(_N)
├── .env                     # actual secrets (gitignored; not included in this context)
├── README.md                 # full A–Z install guide (Linux + Windows), troubleshooting
├── models/                   # downloaded model weights (not committed; see README §5)
│   ├── vosk-model-small-en-us-0.15/     # offline STT
│   ├── en_US-amy-medium.onnx(.json)     # offline TTS, female (Piper)
│   └── en_US-ryan-medium.onnx(.json)    # offline TTS, male (Piper)
├── .claude/
│   ├── PROJECT.md            # the software-side design brief (architecture, expo rules)
│   └── specs/                # per-module specs mirroring the source file headers
│       ├── 01-hearing.md
│       ├── 02-brain.md
│       └── 03-voice.md
└── body/                      # HARDWARE subproject — physical robot, separate concern
    ├── PROJECT.md              # hardware-side design brief, division of labor
    ├── parts-list.md            # BOM rev 2, with per-part purpose + approx. prices
    ├── parts-list.pdf            # same, PDF form
    ├── specs/
    │   ├── 01-audio.md            # USB mic/speaker mounted IN the body, OS-level routing
    │   ├── 02-firmware.md         # ESP32 firmware contract: intent in, motion/light out
    │   ├── 03-electronics.md      # BOM, pinout, wiring, power budget (rev-1, jaw+button)
    │   ├── 04-bringup.md          # staged bench → integration → soak bring-up procedure
    │   ├── 05-presentation-image-prompt.md  # AI image-gen prompt for a marketing hero shot
    │   ├── 06-neck-motion.md      # neck-only spec (pan+tilt, 2 servos) — SPEC, not yet merged
    │   └── 07-arms-motion.md      # arms-only spec (6 servos incl. elbows) — SPEC, mostly built
    └── Rebotic/                    # ACTUAL Arduino/PlatformIO firmware source (untracked, new)
        ├── Rebotic.ino              # setup()/loop() wiring only
        ├── config.h                 # pins, servo calibration, WiFi/MQTT/timing constants
        ├── net.h / net.cpp          # WiFi (WiFiManager portal) + MQTT (mDNS broker lookup)
        └── arms.h / arms.cpp        # the 6-servo arm gesture/animation engine
```

**Important nuance:** the `body/specs/06-neck-motion.md` and `07-arms-motion.md` files
are *design specs*, and the actual firmware in `body/Rebotic/` currently implements
**only the arms** (`arms.h/.cpp`, 6 servos: shoulder-lift, shoulder-rotate, elbow ×2
sides). The neck (pan/tilt, GPIO 14/27) is specified but **not yet wired into this
firmware** — those GPIOs are explicitly reserved and left alone in `config.h`. If asked
to "add the neck to the firmware," the task is to merge spec 06's behavior into the
existing `net.h`/`arms.h` pattern (a new `neck.h/.cpp` module + wiring it into
`Rebotic.ino`'s `armsOnCommand`/`armsTick` calls), not to build from scratch.

---

## 3. The conversation state machine (owned entirely by `main.py`)

```
1. IDLE        ESP32 blinks/sways. Mic open, VAD listening.
2. DETECT      VAD speech_start → MQTT {state:"attend"} → head turns
3. TRANSCRIBE  Audio streams to Vosk, partials arrive live
4. END TURN    VAD silence ~400ms (or PTT release) → finalize transcript
5. THINK       MQTT {state:"thinking"} → filler audio ("hmm…") plays NOW
6. GENERATE    LLM cascade streams tokens
7. SPEAK       First clause → TTS → audio; RMS → MQTT {amp:0-255} @ 40Hz
8. INTERRUPT   Speech detected any time in steps 6-7 → kill TTS, abort LLM, goto 2
9. RESET       MQTT {state:"idle"} → goto 1
```

The **filler trick** (step 5) is the single highest-leverage design decision in the
project: the very first thing `brain.py`'s `respond()` generator yields is a random
filler word/phrase (e.g. `"hmm... "`), *before* any LLM call has even started. Because
it's pre-synthesized to cached PCM at boot (`voice.py`'s `_filler_cache`), it plays with
effectively zero latency, masking the ~500–800ms it typically takes an LLM to produce
its first real token. If the LLM is still not responding after `FILLER_CHAIN_S` (1.2s,
default), `brain.py` chains in another (different) filler rather than leaving dead air,
up to `FILLER_CHAIN_MAX` (3) times.

`main.py`'s `Rebotic` class holds this state via the bus consumer loop (`_consume`),
dispatching on `Event.type`:

| event | meaning | main.py's reaction |
|---|---|---|
| `speech_start` | VAD or PTT fired | `robot.state("attend")` |
| `partial` | live in-progress transcript (debug only) | logged, no action |
| `barge_in` | visitor spoke while the bot was talking | `_interrupt()`: `brain.abort()` + `voice.kill()` |
| `new_session` | quick PTT tap (< `PTT_TAP_MAX_MS`) | `_start_new_session()`: reset history, ask visitor's name |
| `final` | finalized transcript + pitch-based gender guess | drives voice gender, then starts `_turn(text)` (or handles a pending name-ask reply) |

A **visitor session** is the concept that a fresh PTT tap starts a "new visitor" —
`main.py` resets `brain.py`'s history and `voice.py`'s locked gender, then asks
`"Hey! I'm Rebotic. What's your name?"` before resuming normal chat (see §10 for the
VIP special case layered on top of this).

---

## 4. Module-by-module deep dive

### 4.1 `bus.py` — the event bus

Thin wrapper over one `asyncio.Queue`. `Event` is a `@dataclass(type: str, data: dict)`.
Typed constructor functions (`speech_start()`, `partial(text)`, `final(text, gender)`,
`barge_in()`, `new_session()`) are the only way events get created, so every producer
and consumer agrees on shape. `publish_threadsafe()` exists for the `sounddevice` audio
callback thread, which cannot safely touch the asyncio loop directly.

### 4.2 `config.py` — every tunable, one file

Loaded once via `python-dotenv` (`load_dotenv()`), then grouped into sections:
secrets, MQTT, Hearing (spec 01), Brain (spec 02), Voice (spec 03), and a
language-switch block at the bottom. Every constant has an inline comment
explaining the reasoning/tradeoff, not just what it is — e.g. `VAD_SILENCE_MS = 400`
is annotated "< 300 cuts people off, > 600 feels laggy." **This file is the single
source of truth for tuning behavior; never hardcode a magic number that belongs here.**

A helper, `_collect_keys(prefix)`, reads `PREFIX`, `PREFIX_2`, `PREFIX_3`, ... from
`.env` — this is how multiple Groq/Tavily API keys (= multiple free-tier accounts =
multiple quota buckets) get rotated through on rate limits. Both `GROQ_API_KEYS` and
`TAVILY_API_KEYS` use this pattern.

Key config knobs worth knowing up front (full list lives in the file):
- `LLM_BACKEND`: `"groq"` (cloud+fallback, default) | `"ollama"` (offline only) | `"canned"`
- `INPUT_MODE`: `"both"` (VAD+PTT, default) | `"vad"` | `"ptt"`
- `TTS_ONLINE_VOICES` / `STT_ONLINE_MODEL`: empty = that online tier is disabled entirely
- `LANGUAGE`: `"en"` (default, fully wired) or a dormant `"ur"` (Urdu) mode — see §9

### 4.3 `nethealth.py` — shared circuit breaker

One module-level singleton, `health = NetHealth(config)`, with `.ok: bool` and a public
`.mark_down()`. Any cloud call that fails with a network-shaped error (`TimeoutError`,
`ConnectionError`, Groq's `APIConnectionError`, `httpx.TransportError`) calls
`health.mark_down()`. That flips `.ok = False` and spawns a background asyncio task that
probes `api.groq.com:443` (TCP connect only) every `CLOUD_PROBE_INTERVAL_S` (10s); the
first successful probe flips `.ok` back to `True`. Both `brain.py` and `voice.py` check
`health.ok` before attempting their cloud tier, so a Groq outage and an edge-tts outage
recover independently but through the exact same mechanism, and either one tripping
takes both tiers offline together (since a dead internet connection kills both).
`health.simulate_offline` is a manual test hook used by `brain.py`'s REPL `/offline` command.

### 4.4 `hearing.py` — Spec 01: mic → transcript events

**Pipeline:** mic (`sounddevice.InputStream`, 16kHz mono int16, 512-sample/32ms frames,
required exactly by Silero) → Silero VAD (`onnx=True`, ~1ms/frame on CPU) → Vosk
(`KaldiRecognizer`, streaming partials + finals) → events on the bus. Vosk's transcript
is always computed and is the instant fallback; if `STT_ONLINE_MODEL` is configured (Groq
Whisper, default `whisper-large-v3-turbo`) and the network breaker is up, the turn's raw
audio is *also* sent to Groq in parallel, and its transcript **replaces** Vosk's if it
lands within `STT_ONLINE_TIMEOUT_S` (2.5s) — otherwise the Vosk text ships as-is. A miss
here also trips the shared breaker.

**VAD state machine** (hysteresis, not a single threshold):
```
SILENT --speech prob > VAD_THRESHOLD_ON (0.5) for 2 frames--> SPEAKING
SPEAKING --speech prob < VAD_THRESHOLD_OFF (0.35) for VAD_SILENCE_MS--> SILENT (finalize)
```
`VAD_SILENCE_MS` (400ms default) is called out repeatedly across the docs as *the single
most impactful tunable in the whole project*. There's also a smarter version:
`_required_silence_ms()` checks whether the last word heard is in
`config.INCOMPLETE_TAIL_WORDS` (articles, conjunctions, prepositions, question words,
hesitations like "uh"/"um") — if so, it waits the longer `VAD_SILENCE_HOLD_MS` (1100ms)
instead, because a pause after "what is the…" is a mid-thought pause, not the end of a
turn. This is a deliberate human-turn-taking heuristic, not a bug.

**Push-to-talk (PTT)** is implemented via `pynput`'s global spacebar listener (dev) and
is explicitly called out as the **primary** input at an expo, not a fallback — VAD
misfires constantly in a loud hall. `INPUT_MODE = "both"` means PTT overrides VAD while
held. A **short tap** (held under `PTT_TAP_MAX_MS`, 300ms default) is special-cased as a
"new visitor" signal (`_cancel_as_tap()` → publishes `new_session` instead of a normal
transcript) — the idea being a full press-and-hold is a spoken turn, a quick tap is a
deliberate "start over" gesture.

**Barge-in / echo mitigation:** while the bot is speaking (`hearing.robot_speaking =
True`, set by `main.py`), VAD is *not* disabled — `speech_start` firing during playback
publishes `barge_in` instead of a normal turn-start. But because an open laptop mic
picks up its own speaker output, `VAD_BARGE_IN = False` is the shipped default — the mic
keeps listening but VAD-detected speech during playback is *suppressed entirely* unless
a headset/directional mic is confirmed not to self-trigger. **PTT always interrupts
regardless of this flag.** `BARGE_IN_SUPPRESS_MS` additionally mutes VAD for a short
window at the start and end of TTS playback (reverb tail).

**Gender detection** (`_estimate_gender`): per-window YIN pitch estimation (cumulative
mean normalized difference, chosen over raw autocorrelation because it doesn't
octave-jump onto harmonics) over the turn's captured audio, with a harmonic-guard
correction step. Uses the **25th percentile** F0 across voiced windows (not the median)
because pitch-estimation errors here systematically bias *upward* (octave errors, pitch
glides), so the low tail of the distribution is the more trustworthy statistic. Below
`PITCH_MALE_MAX_HZ` (165Hz) → `"male"`, above → `"female"`, too few confident voiced
windows (`PITCH_MIN_VOICED_WINDOWS`, 5) → `"unknown"` (keeps whatever voice was already
selected).

**Junk filtering** (every rejected transcript is one fewer LLM API call, i.e. quota
saved): reject if text < `MIN_TRANSCRIPT_CHARS` (2), speech duration <
`VAD_MIN_SPEECH_MS` (250ms, rejects coughs/clicks), or text is exactly in `JUNK_SET`
(`{"the","a","uh","huh",""}`). Truncate to `MAX_TRANSCRIPT_CHARS` (200).

**Emits on the bus:** `speech_start`, `partial(text)`, `final(text, gender)`,
`barge_in`, `new_session`. Never touches `brain.py` directly.

### 4.5 `brain.py` — Spec 02: transcript → streamed reply tokens

**Provider cascade**, tried in order, every layer abortable and never leaving the
conversation silent:
```
1. Groq llama-3.3-70b-versatile   (every configured key rotated first)
2. Groq llama-3.1-8b-instant      (separate RPD quota bucket from 70B; every key rotated)
3. Ollama qwen2.5:0.5b-instruct   (fully offline, local; ~600ms+ TTFT on CPU)
4. Canned scripted answers        (fuzzy-matched via difflib; last resort, never fails)
```
`_attempts()` builds the `(key_index, model)` pairs as "rotate key first, then downgrade
model" — i.e. key1+70B → key2+70B → key1+8B → key2+8B → ... — because a rate-limit on
one key doesn't mean the model is bad, it means that specific account's quota is spent.
Network-shaped errors (`_NETWORK_ERRORS`) skip straight past all remaining Groq
attempts to Ollama (rotating keys won't fix a dead network) and trip `health.mark_down()`.
Non-network errors (e.g. a malformed response) just move to the next attempt.

**Streaming is mandatory and abortable.** `_stream()` yields the filler immediately,
then races the cascade's first token against `FILLER_CHAIN_S` using `asyncio.wait`
(not `wait_for`, deliberately — `wait_for` would cancel the pending call on timeout,
but here the slow cascade must keep running underneath while more fillers chain in).
`self._abort: asyncio.Event`, checked between every yielded chunk; `main.py`'s barge-in
handler calls `brain.abort()`.

**History management:** hard-trimmed every turn to `HISTORY_TURNS * 2` messages (4
turns = 8 messages default) — unbounded history over a 6-hour expo means RAM growth,
rising latency, and rising token spend, all compounding. Auto-resets
(`if now - last_interaction > IDLE_RESET_S: reset()`) after 60s of silence so one
visitor's odd conversation doesn't bleed into the next visitor's.

**Live web search (Tavily), optional:** `_needs_search()` regex-matches the transcript
for search-shaped intent ("current", "latest", "who is/won", "price of", "weather",
"score", named political titles, etc.) before paying Tavily's latency at all. A direct
Tavily API call (not Groq's built-in search model, which was measured unreliable and
carries a fixed ~1.2s tax) folds real search snippets into the Groq request as extra
system-role context, so the trusted fast model still does the actual answering. Rotates
across `TAVILY_API_KEYS` on failure, never raises out of `_tavily_search`, never trips
the network breaker on its own failure (Tavily being down ≠ the internet being down).
No key configured = tier silently disabled, same "app still works without it" pattern
as everything else.

**The local (Ollama) tier gets special treatment**, because a 0.5B-parameter model
measurably behaves worse than the cloud models and needs to be handled carefully:
- A **compact, separate system prompt** (`OLLAMA_SYSTEM_PROMPT`) and only
  `OLLAMA_HISTORY_TURNS` (3) of history — every token the CPU re-reads is latency.
- The visitor's name and a context-reread hint are injected into the **user** message,
  never the system prompt — verified live that small models ignore facts injected into
  the system prompt mid-conversation (the model answered "what's my name?" with its own
  name, "Rebotic," instead of the visitor's, until this was fixed). This is documented
  in-code as a specifically tested and confirmed fix, not a guess.
- `warm_ollama()` fires at boot (during the greeting) to pre-load the model and
  pre-evaluate its system prompt into llama.cpp's prefix cache, with `keep_alive: "6h"`,
  so a mid-demo Groq outage doesn't also pay a cold-start Ollama penalty.

**Special-guest welcome:** `welcome_special_guest(guest)` is a separate cascade
invocation (same filler/abort machinery) used only when a visitor's spoken name matches
the VIP roster (see §10) — it uses a longer `SPECIAL_GUEST_MAX_TOKENS` (160 vs. the
normal 80) and skips the search-hint check (`allow_search=False`) because VIP titles
like "Chief Minister" spuriously match the search regex even though the answer is
already fully specified in the prompt.

**`_extract_name(text)`:** best-effort name-pull from a raw transcript — strips leading
hesitations ("uh, um, well, so"), then lead-in phrases ("my name is", "I'm", "it's",
"call me"), caps at 2 words, and rejects if the first word is a stopword ("no", "yes",
"hi", "sorry", etc. — things a mic could easily mis-hear as a name attempt). Returns
`None` on any doubt; the caller (`main.py`) decides whether to retry or give up, never
guesses wrong on purpose.

### 4.6 `voice.py` — Spec 03: tokens → audio + mouth-sync

**Two synthesis tiers, chosen per-clause** by the shared breaker:
- **Online:** Microsoft neural voices via `edge-tts` (near-human quality), returns mp3,
  decoded to raw PCM via a subprocess call to `ffmpeg` (resampled to Piper's native
  22050Hz so downstream code — playback, amp calculation — never needs to know which
  tier spoke).
- **Local:** Piper ONNX voices (`PiperVoice.load`), always loaded at startup, guaranteed
  available offline. Two gendered voices loaded (`en_US-amy-medium` female,
  `en_US-ryan-medium` male by default).

A failed or slow (`> TTS_ONLINE_TIMEOUT_S`, 4.0s) online synth falls back to Piper
**mid-turn** and trips `health.mark_down()`, so the voice degrades gracefully instead of
going silent on that clause.

**Clause chunking is the core latency trick, mirroring the filler trick in `brain.py`:**
tokens are buffered until a sentence-ending boundary char (`CHUNK_ON = ".!?۔؟"` — note:
**not** commas/semicolons; those used to be boundaries but gave every clause its own
TTS call with audible prosody seams, so only whole sentences are flushed as one unit)
AND the buffer is at least `CHUNK_MIN_CHARS` (12) long (so `"Wow!"` doesn't flush alone —
it merges into the next boundary). The **first clause starts playing while the LLM is
still generating the rest of the reply** — this is where the sub-1-second number
actually comes from.

**Gesture tags:** the LLM is allowed to emit `[nod]`, `[tilt]`, `[shrug]` inline in its
reply (system prompt instructs "at most one per reply"). `_speak_clause()` regex-extracts
these (`_GESTURE_RE = re.compile(r"\[(\w+)\]")`), publishes each as an MQTT `gest`
message, and **strips them before synthesis** — otherwise Piper would literally speak
"bracket nod bracket."

**Mouth-sync (amplitude tap):** playback runs through one persistent
`sounddevice.OutputStream` whose callback pulls PCM from a shared byte buffer;
`blocksize` is deliberately set to `TTS_RATE // AMP_HZ` (22050/40 ≈ 551 samples) so
*every single audio callback is exactly one amp frame* — RMS of that block → clipped/
scaled to 0-255 → EMA-smoothed (`AMP_SMOOTH = 0.3`, because raw RMS makes a jaw chatter)
→ published to MQTT at 40Hz for free, with zero extra buffering logic. An empty block
(buffer ran dry) always force-publishes `amp:0` — the single most visibly-broken failure
mode this project explicitly guards against is a jaw frozen open.

**Gender-matched voice, locked per session:** `set_gender()` only accepts the *first
confident* detection per conversation (`_gender_locked`), because per-utterance pitch
estimation is noisy enough that re-deciding every turn made the voice audibly flip-flop.
A gap over `IDLE_RESET_S` (60s, same constant `brain.py` uses for history reset) unlocks
it for a presumed-new visitor. `"unknown"` guesses never change or unlock anything.
`reset_gender()` is the explicit version, called on a PTT-tap new session.

**Killable, instantly:** `kill()` sets the abort event, clears the pending PCM buffer
under the lock (without this, killed audio would resume audibly ~200ms later from
whatever was already queued in the OS audio driver), zeroes the amp, and republishes
`state:"attend"`. Cutting mid-word on interrupt is treated as *correct*, not a bug — a
bot that finishes its sentence after being interrupted reads as a machine, not a person.

**Fillers are pre-synthesized to raw PCM at boot** (`_filler_cache`, keyed by normalized
filler text) for both genders, which also happens to warm up both Piper ONNX sessions so
the very first real clause of the whole run doesn't pay Piper's ~400ms cold-start. If
online voices are configured, `warm_online()` (fired concurrently with `warm_ollama()`
during the boot greeting) additionally pre-synthesizes neural-voice filler clips; until
that lands, an online-tier turn's filler simply borrows the Piper clip rather than
paying a network round-trip for something meant to mask latency.

### 4.7 `robot.py` — MQTT publisher, fail-silent by design

Wraps `paho.mqtt.client` with `connect_async` + `loop_start` (never blocks, auto-
reconnects) and three typed methods: `state(v)` (`idle|attend|thinking|speak`),
`amp(v)` (0-255, expected at 40Hz during speech), `gest(v)` (`nod|tilt|shrug`). Every
`_publish` is wrapped in a bare `try/except: pass` — **if the broker is down, the robot
body is simply absent; the voice conversation is completely unaffected.** This is a
deliberate, explicit design principle repeated throughout the docs: hardware is optional,
conversation is not. `python robot.py` run standalone is the MQTT smoke test (watch with
`mosquitto_sub -t rebotic/cmd -v`).

### 4.8 `main.py` — wiring, state machine, latency instrumentation

Owns the `Rebotic` class: constructs `Bus`, `Robot`, `Hearing`, `Brain`, `Voice`, loads
the guest roster, and runs the `_consume()` event loop described in §3. Also owns:
- **Boot sequence:** kicks off `brain.warm_ollama()` and `voice.warm_online()` as
  background tasks, plays the greeting (**before** the mic opens, so the speaker can't
  immediately barge itself in), then opens the mic and starts consuming events.
- **Graceful shutdown** on `SIGINT`: aborts any in-flight brain/voice task, stops
  hearing, closes voice's audio stream, zeros the robot's amp, sets state idle,
  disconnects MQTT.
- **`--latency` flag:** taps `robot.amp()` to timestamp the first non-zero amplitude
  publish (`_t_first_audio`), and instruments `brain.respond()`'s token stream
  (`_tap()`) to timestamp the first *real* LLM token (skipping the filler). Prints a
  per-turn breakdown: `speech_end→final` (fixed at `VAD_SILENCE_MS` by construction),
  `final→first_token`, `final→first_audio`, `final→done`, and the headline number
  `speech_end→first_audio` against the < 1000ms target.
- **`--debug` flag:** verbose logging (partial transcripts, provider fallback reasons).

---

## 5. MQTT contract (the entire laptop↔body interface)

Broker: `localhost:1883` (on the laptop; the body's WiFi is the same LAN or the laptop's
own hotspot — see hardware bring-up notes). Topic: `rebotic/cmd`, QoS 0 (fire-and-forget
— intent messages are cheap and frequent enough that a dropped one just means the next
one corrects it).

```json
{"t":"state","v":"idle|attend|thinking|speak"}
{"t":"amp","v":0-255}              // 40Hz during speech only, published from voice.py
{"t":"gest","v":"nod|tilt|shrug"}  // one-shot overlay gestures, parsed from LLM output
```

**Governing rule, stated explicitly and repeatedly across every doc in this repo: the
laptop sends *intent*, never raw servo angles.** Network jitter over WiFi would make
direct angle control visibly twitch; the ESP32 firmware owns all timing, easing,
interpolation, and servo safety locally. If you're ever asked to add a new laptop→body
message, it must describe a *state* or a *one-shot event*, not a numeric joint position.

A reverse topic, `rebotic/evt`, is **specified but not yet implemented on the laptop
side**: `{"t":"ptt","v":"down|up"}` for a hardware push-to-talk button on the body. The
firmware-side publish exists in spec (`body/specs/02-firmware.md`); the corresponding
laptop-side MQTT *subscriber* in `robot.py`/`hearing.py` is explicitly called out as
"the one future change that touches the software tree" and is deliberately left
unwritten until someone explicitly asks for it. If asked to build this: it needs to
drive the exact same code path as the spacebar PTT already in `hearing.py`'s `_set_ptt`.

---

## 6. Hardware: the "body" subproject

**Golden rule, stated in `body/PROJECT.md`:** the laptop is the mind, `body/` is the
body, and they talk over a wire (USB audio) and the air (MQTT intents) — **nothing in
`body/` ever requires editing a `.py` file in the repo root**, and vice versa.

### 6.1 Division of labor

| | Mind (laptop) | Body (ESP32 + physical parts) |
|---|---|---|
| Hearing/thinking/voice synthesis | all of it (Python) | — |
| Microphone | opens OS default input | USB mic **mounted in the body**, cabled to laptop |
| Speaker | opens OS default output | powered speaker **mounted in the body**, cabled to laptop |
| Motion, eyes | publishes MQTT intent | ESP32 + servos + WS2812B LEDs, all timing/easing local |
| Push-to-talk | consumes `rebotic/evt` (not yet wired) / spacebar today | — (current build has no physical PTT button; see parts-list rev 2) |

### 6.2 Why zero software changes are needed for body-mounted audio

`hearing.py` opens `sounddevice.InputStream` and `voice.py` opens
`sounddevice.OutputStream` **without naming a device** — both use the OS default
source/sink. Making the body's USB mic and speaker the OS defaults (`pactl
set-default-source` / `set-default-sink`, works on both PulseAudio and PipeWire)
reroutes every sample through the body; the Python code cannot tell anything moved.
This must be done **before** `main.py` starts (already-open streams keep their old
device). Rollback is the same two commands pointed back at the laptop/headset.

### 6.3 Current hardware state — what's actually built vs. only specified

This distinction matters a lot for any code-generation request:

| Subsystem | Status |
|---|---|
| USB mic + speaker in the body | **Spec 01** — hardware/OS-config only, no code |
| MQTT contract (`state`/`amp`/`gest`) | **Implemented** on both sides — `robot.py` (laptop) and `net.h/.cpp` (ESP32) |
| Arms (6 servos: shoulder lift + rotate + elbow, ×2 sides) | **Implemented** — `arms.h/.cpp`, per spec 07 |
| Neck (pan/tilt, 2 servos) | **Specified only** (spec 06) — GPIOs 14/27 reserved, not yet in firmware |
| Jaw servo / mouth movement | **Removed from scope** (parts-list rev 2) — replaced conceptually by a future OLED face, not a physical jaw. `amp` is still published and received by firmware but explicitly ignored (no jaw hardware exists) |
| Hardware PTT button | **Removed from scope** (parts-list rev 2) — push-to-talk stays software-only (spacebar today; `rebotic/evt` glue is future work if a button is ever added back) |
| Eyes (WS2812B) | **Specified** (spec 02/03), hardware on the parts list — check current firmware for whether LED driving is implemented alongside `arms.cpp` |
| WiFi provisioning | **Implemented, dynamic**: `WiFiManager` captive-portal, no hardcoded SSID/password. First boot (or BOOT-button reset) opens an AP named `Rebotic-Setup`; credentials entered via a captive setup page persist to flash |
| Broker discovery | **Implemented via mDNS**: firmware looks up the laptop's mDNS hostname (`aiwix`, in `config.h`'s `MQTT_BROKER_MDNS_NAME`) rather than a hardcoded IP, so it survives network changes as long as laptop and ESP32 share a LAN |

### 6.4 Firmware architecture (`body/Rebotic/`)

```cpp
Rebotic.ino   // setup(): esp_task_wdt init, netBegin(armsOnCommand), armsBegin()
              // loop():  esp_task_wdt_reset(), netLoop(), 10s-no-cmd→force-idle check, armsTick(now)
config.h      // WiFiManager portal name/timeout, mDNS broker hostname, MQTT topic/client-id,
              // per-servo GPIO pins + ServoCal{base, minAngle, maxAngle, mirror} calibration structs,
              // NO_CMD_TIMEOUT_MS (10s), SERVO_DETACH_IDLE_MS (5s), WDT_TIMEOUT_S (8s)
net.h/.cpp    // non-blocking WiFi+MQTT connect/reconnect, JSON parse of rebotic/cmd,
              // dispatches {t, v} pairs to a registered handler; tracks netLastCmdMillis()
arms.h/.cpp   // armsBegin() (attach + ease to neutral over ~1s), armsTick(now) (pure function
              // of millis(), drives idle/attend/thinking/speak animation or an in-flight
              // gesture), armsOnCommand(t, v) (handles "state"/"gest", ignores "amp" — no jaw)
```

Right-side servos physically mirror the left side; a `mirror: true` flag in `ServoCal`
remaps `angle → 180 - angle` at write time rather than requiring different logic per side.

**Motion design language** (documented in specs 06/07, applies to any future firmware
work): every joint move is a value sampled from an eased curve over time — never a bare
`servo.write()` jump. Idle states have a constant small sinusoidal "breathe" (~±3° at
0.2Hz) so the robot is never perfectly still. Multi-joint gestures **stagger** each
joint's start time by 50-160ms (shoulder leads, elbow trails) so a 3-servo arm reads as
"one arm moving," not "three motors." Gesture amplitude/duration gets small random
jitter (±10%/±15%) rolled once per trigger so no two gestures look identical. `nod`
similarly has a small anticipatory wind-up before the real motion. All servos
`detach()` after 5 seconds of no motion demand (a 6-hour expo would otherwise cook SG90
gears holding current continuously) and firmware is watchdog-protected (`esp_task_wdt`,
8s timeout) since it must survive unattended for hours.

### 6.5 Power & electronics essentials

- **External 5V supply for the servo rail, common ground with the ESP32 — never power
  servos from the ESP32's own 5V/VIN pin** (its onboard regulator browns out under
  servo stall current, causing what looks like random reboots).
- Current BOM (parts-list rev 2, 6 arm servos, no jaw/button): ~4A servo worst case,
  5A supply is the floor. If the neck (2 more servos) is ever merged in, specs 06/07
  both flag that the combined 8-servo rail needs a **6A supply minimum**, not 5A.
  Full pin table, BOM with approximate prices, and wiring diagram are in
  `body/specs/03-electronics.md` (still describes the rev-1 jaw+button design — flagged
  in the parts list as needing an update to match rev 2) and `body/parts-list.md`.
- Mic and speaker are laptop USB peripherals that physically live in the body — they
  never connect to the ESP32 electrically. Speaker mass stays in the static chest, not
  the moving head (servos can't sling a speaker around without cable fatigue).

### 6.6 Bring-up order (`body/specs/04-bringup.md`)

Staged, gated procedure — each stage must fully pass before advancing, because later
symptoms become ambiguous if an earlier stage was skipped: **(0)** body audio routing via
`pactl`, verified with the existing standalone `voice.py`/`hearing.py` tests, zero code
touched; **(1)** Mosquitto opened to the LAN (it binds `localhost` only by default —
the classic blocker) and reachable from the ESP32's hotspot; **(2)** bench electronics
(eyes → servos → button) one subsystem at a time; **(3)** firmware↔broker via
`mosquitto_pub`/`robot.py` smoke tests; **(4)** the full loop, `python main.py --latency`,
gated on `speech_end→first_audio` still under 1000ms with the body attached; **(5)**
hardware PTT (blocked on the laptop-side `rebotic/evt` glue, not yet written); **(6)** a
pre-expo soak test (1hr+ continuous run, WiFi-pull recovery, USB replug recovery).

---

### 6.7 Communication toolchain — exactly what talks to what, and how

There are **two, completely separate, physical channels** between the laptop and the
robot body, using different transports, different tools, and carrying different kinds
of data. Nothing about the audio channel touches the motion channel or vice versa.

**Channel A — audio (USB, wired, no code):** the USB microphone and USB-powered speaker
are ordinary USB audio peripherals plugged straight into the laptop and made the OS
default input/output device via `pactl set-default-source`/`set-default-sink`
(PulseAudio/PipeWire). `sounddevice` (Python) opens the OS default device with no device
name specified, so this is 100% an OS/hardware concern — no network, no MQTT, no
firmware involvement at all (see §6.2).

**Channel B — motion/light intent (WiFi + MQTT):** this is the one that involves actual
network communication and two separate software stacks talking a shared protocol.

```
┌──────────────────────────────┐        WiFi (802.11, local LAN or         ┌───────────────────────────┐
│   LAPTOP  (Linux, "aiwix")    │        laptop's own phone hotspot)         │   ESP32 body controller    │
│                                │◄───────────────────────────────────────────►│                             │
│ Mosquitto broker  :1883        │           MQTT over TCP, QoS 0              │ WiFi station + mDNS client │
│  (listener 1883, LAN-bound     │                                             │                             │
│   per body/specs/04-bringup)   │──── publish rebotic/cmd ───────────────────►│ PubSubClient subscriber    │
│                                │                                             │  → armsOnCommand()          │
│ robot.py (paho-mqtt client)    │◄──── (rebotic/evt, spec'd, NOT yet wired)──│ (would publish PTT edges)  │
│  publisher only, no subscriber │                                             │                             │
└──────────────────────────────┘                                             └───────────────────────────┘
```

| Layer | Laptop side | ESP32 side |
|---|---|---|
| Physical transport | WiFi (whatever LAN/hotspot the laptop is on) | Same WiFi network (ESP32 station mode) |
| Application protocol | **MQTT** (publish/subscribe over TCP) | **MQTT** |
| Client library | `paho.mqtt.client` (Python, `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)`), used only in `robot.py` | `PubSubClient` (Arduino/C++), used only in `net.cpp` |
| Broker (server) | **Mosquitto**, runs *on the laptop itself* (`localhost:1883` from the laptop's own point of view; opened to `0.0.0.0:1883` on the LAN per bring-up spec so the ESP32 can reach it) | connects out to the broker as a client — the ESP32 never runs a broker |
| Broker discovery | n/a — Mosquitto is local, `config.MQTT_HOST = "localhost"` | **mDNS**: `ESPmDNS`'s `MDNS.queryHost("aiwix", timeoutMs)` resolves the laptop's mDNS hostname (`MQTT_BROKER_MDNS_NAME` in `config.h`, currently `"aiwix"`) to its current IP, instead of a hardcoded address — re-resolved automatically if a connect attempt fails (DHCP lease changes, laptop reboot, etc.). *(This relies on the laptop actually advertising `aiwix.local` over mDNS — on Linux that's normally `avahi-daemon`, already running by default on most desktop distros including Ubuntu; nothing in this repo configures it explicitly, so if the ESP32 can't resolve the broker, checking `avahi-daemon`'s status on the laptop is the first thing to look at.)* |
| Message format | `json.dumps({"t": ..., "v": ...})` → published as a UTF-8 string payload | `ArduinoJson` (`JsonDocument`, v7 auto-sized) `deserializeJson()` parses the payload back into `{t, v}` |
| Topic(s) | publishes `rebotic/cmd` (`config.MQTT_TOPIC`); does **not** subscribe to anything | subscribes `rebotic/cmd` (`MQTT_TOPIC_CMD` in `config.h`); would publish `rebotic/evt` for hardware PTT once that feature is built (see §7) |
| QoS | 0 (`config.MQTT_QOS = 0`) — fire-and-forget, matches `PubSubClient`'s default QoS 0 | 0 |
| Delivery guarantee | **None, deliberately.** A dropped `amp` frame just means the next 40Hz tick corrects the jaw a moment later; a dropped `state` message is superseded by the next state change. Nothing in this protocol needs at-least-once delivery. | same |
| Failure behavior | `robot.py._publish()` wraps every send in a bare `try/except: pass` — broker unreachable = silent no-op, conversation pipeline totally unaffected | `net.cpp`'s `mqttServiceNonBlocking()`/`wifiServiceNonBlocking()` retry with backoff, never block `loop()`; firmware falls back to its `idle` animation and keeps trying |

**Other tools/libraries involved on the ESP32 firmware side, and exactly what each one is for:**

| Tool / library | Role |
|---|---|
| `WiFiManager` | Captive-portal WiFi provisioning — no SSID/password compiled into firmware. Opens an AP (`Rebotic-Setup`) with a web config page on first boot or when the saved network is unreachable; credentials persist to flash after that. See `net.cpp`'s `wifiConnectBlocking()`/`wifiServiceNonBlocking()`. |
| `ESPmDNS` | Resolves the laptop's mDNS hostname to an IP (broker discovery, see table above) and registers the ESP32 itself as `rebotic-arms.local` (`MDNS.begin("rebotic-arms")`). |
| `PubSubClient` | The actual MQTT client implementation — connect, subscribe, publish, and the message-received callback plumbing. |
| `ArduinoJson` (v7) | Parses incoming `rebotic/cmd` JSON payloads into `{t, v}` C strings. |
| `ESP32Servo` | LEDC (ESP32 hardware PWM peripheral)-backed servo driver — every `Servo.attach()`/`.write()`/`.detach()` call in `arms.cpp` goes through this. |
| `esp_task_wdt` (ESP-IDF) | Hardware watchdog timer, 8s timeout (`WDT_TIMEOUT_S`) — firmware must call `esp_task_wdt_reset()` every `loop()` iteration or the chip force-reboots. Exists because the firmware runs unattended for hours at an expo. |
| Arduino `Serial` (115200 baud) | Debug logging only, over the same USB cable used to flash the board — not part of the runtime laptop↔body link (that's WiFi/MQTT). |

**What is explicitly NOT used, on purpose:** no HTTP/REST between laptop and body, no
WebSockets, no raw TCP/UDP socket protocol, no Bluetooth. MQTT was chosen because it's a
lightweight pub/sub model that matches the "laptop broadcasts intent, body reacts"
shape exactly, degrades gracefully (QoS 0, no handshake required per message), and
libraries exist on both the Python and Arduino sides that need almost no glue code.

---

## 7. Language support: the `en`/`ur` switch

`config.LANGUAGE = "en"` (default) leaves the American persona, English Vosk model,
English fillers/canned-answers, and English-tuned turn-taking word list untouched. A
dormant `LANGUAGE = "ur"` (Urdu) block at the bottom of `config.py` swaps every
language-dependent piece at once: STT model path (points at a Hindi Vosk model, since
spoken Hindi and spoken Urdu are the same language — Hindustani — and Vosk transcribes
in Devanagari script, which the LLM is then instructed to treat as Urdu and always reply
to in Urdu Arabic script), the incomplete-tail-word list (Devanagari tokens), a fully
rewritten Pakistani-persona system prompt, Urdu fillers, Devanagari-keyed canned answers,
and Urdu Piper/edge-tts voice model names. **Re-enabling `"ur"` requires downloading the
Urdu model files separately** — they are not included in the standard model-download
step in the README. If asked to add a third language, follow this exact pattern: one
`if LANGUAGE == "xx":` block overriding every language-dependent constant, not scattered
per-module `if` branches.

---

## 8. The special-guest (VIP) feature

`special-gest.md` is a small, human-editable roster file, one blank-line-separated block
per guest:
```
## Full Name
Title: their title
Background: one paragraph
Contributions: one paragraph
```
`guests.py` parses it into `Guest` dataclasses at boot (`guests.load(config.SPECIAL_GUEST_FILE)`)
and offers two matchers on the resulting `Roster`: `match(spoken_name)` (an already-
extracted ≤2-word name) and `match_in_text(full_transcript)` (scans the raw transcript
for roster name tokens — VIPs often introduce themselves with a title attached, e.g.
"I'm the Chief Minister, Sohail Afridi," which the generic 2-word `_extract_name`
extractor would otherwise mangle into just "Chief Minister"). Both matchers require
**≥2 overlapping name tokens** (or a single-token name matching exactly) before
returning a hit — a wrong VIP greeting in front of an audience is considered worse than
falling back to the generic "nice to meet you" ack, so ambiguous matches (e.g. a shared
surname across multiple roster entries) intentionally return `None`.

When `main.py`'s new-visitor-session flow gets a name reply, it checks
`guests.match_in_text()` **before** running the generic name extractor. A hit routes to
`brain.welcome_special_guest(guest)` — a longer, more ceremonial LLM-generated welcome
(`SPECIAL_GUEST_PROMPT_TEMPLATE`, `SPECIAL_GUEST_MAX_TOKENS = 160`) required to mention
one specific fact from *both* the guest's background and their contribution, so they
feel recognized rather than generically greeted.

---

## 9. Setup & installation (condensed — full A–Z guide is in `README.md`)

Both Linux (Ubuntu 24.04, actually run) and Windows 10/11 (standard installers,
untested against this exact machine) are documented step by step in the README. High
points any AI helping with setup issues should know:

1. **Python 3.12**, a mic + speaker (laptop built-ins are fine for dev), a free Groq API
   key (works without one, but with reduced quality/reliability — see fallback chain).
2. System packages: `portaudio19-dev` (sounddevice's native dep), `ffmpeg` (decodes
   edge-tts's mp3 output), `espeak-ng` (Piper's phonemizer), `mosquitto-clients`
   (optional, MQTT testing).
3. `pip install -r requirements.txt`, **then separately** `pip install torch torchaudio
   --index-url https://download.pytorch.org/whl/cpu` — **critically, never plain `pip
   install torch`**, since the default PyPI wheel is a CUDA build that fails to import
   (`libcudart.so`/DLL errors) on a CPU-only machine, which this project always assumes.
4. Download models into `models/`: two Piper ONNX voices (~60MB each) and the Vosk
   small English model (~40MB) — exact `wget`/`curl.exe` commands are in the README.
5. `cp .env.example .env`, fill in `GROQ_API_KEY` (required for the cloud tiers;
   app still runs without it, falling through to Ollama/canned) and optionally
   `TAVILY_API_KEY` (live search) and additional numbered keys for more quota headroom.
6. Install Ollama, `ollama pull qwen2.5:0.5b-instruct` — this is the offline LLM
   fallback and is what answers when the internet is down or slow. The README
   explicitly warns against building on `qwen2:0.5b`/`tinyllama` variants that can't
   hold a conversation, and notes a bigger model like `1.5b-instruct` answers better but
   is noticeably slower on CPU (the 0.5b default is tuned for first-token latency, not
   eloquence).
7. Mosquitto, only if the physical robot body is being used — `robot.py` fails silently
   with no broker running.
8. `python main.py` — greeting plays, then `"listening — speak, or hold SPACE for
   push-to-talk"`.

Common troubleshooting (also in the README): CUDA torch import errors (redo step 3
above with the CPU index URL); silent speaker (check OS volume, not code — a
surprisingly common false "it's broken" report); mic permission (Windows Privacy
settings); self-interruption on a laptop's built-in mic (expected, `VAD_BARGE_IN =
False` is the fix, use PTT or a headset instead); slow first offline reply (expected on
the very first request after `ollama pull`, before `warm_ollama()` has had a chance to
run at boot — subsequent requests are fast because of `keep_alive: "6h"`).

---

## 10. Running & testing

```bash
python main.py                # full pipeline
python main.py --latency      # + per-turn timing breakdown printed each turn
python main.py --debug        # + verbose logging (partials, provider fallbacks)

# Standalone module tests — each runs completely independently:
python hearing.py             # prints bus events live; speak or hold SPACE
python brain.py               # text REPL against the LLM cascade;
                               #   /offline and /online simulate a network drop mid-chat
python voice.py "some text"   # TTS only, plays through the speaker; --slow simulates
                               #   a token-by-token LLM stream to demo the chunking
python robot.py               # MQTT smoke test; watch with `mosquitto_sub -t rebotic/cmd -v`
```

Every module's own file has a `if __name__ == "__main__":` block implementing its
standalone test — this is a deliberate pattern worth preserving in any new module: each
piece must be independently verifiable without booting the whole pipeline.

---

## 11. Design philosophy / "rules of the project" (do not violate silently)

These are stated explicitly, repeatedly, across `.claude/PROJECT.md` and the specs —
treat them as hard constraints when generating or reviewing code for this repo, not
just style suggestions:

- **Never go silent.** Every layer that can fail has a fallback that cannot: STT
  (Groq Whisper → Vosk), LLM (Groq 70B → Groq 8B → Ollama → canned), TTS (neural voices
  → Piper), MQTT (robot body optional entirely). A change that removes a fallback tier
  without an equally-robust replacement is a regression.
- **Stream, don't wait.** LLM tokens and TTS clauses both start flowing/playing before
  the full response exists. Any code path that buffers a full LLM reply before starting
  TTS defeats the entire latency design.
- **Send intent, never angles/raw values, to the robot body.** The MQTT contract is
  `state`/`amp`/`gest` only.
- **Everything must be abortable mid-flight** (barge-in). Any new long-running async
  operation triggered by a conversation turn (LLM call, TTS synth, a hypothetical future
  tool call) needs to check/respect the shared abort mechanism.
- **History and any other cross-turn state must be bounded and reset on idle.** A
  6-hour continuous demo makes any unbounded growth eventually fatal.
- **Config values, not magic numbers.** Every tunable belongs in `config.py` with a
  rationale comment, not hardcoded inline in a module.
- **Modules communicate only through the bus** (`hearing`/`brain`/`voice` never call
  each other directly) — `main.py` is the sole place that wires cross-module behavior.
  `nethealth.py`'s shared breaker object is the one deliberate exception (passive shared
  state, not a direct call), because both cloud tiers genuinely share one underlying
  resource (the internet connection).
- **Every rejected/filtered input is quota saved.** Junk-transcript filtering in
  `hearing.py` exists specifically because Groq's free-tier daily request cap (RPD) is
  the real ceiling at a multi-hour expo, not per-minute limits.
- **Constrain the persona hard.** The system prompt explicitly forbids lists, caps
  reply length at 1-2 sentences, and instructs deflection with humor for
  off-topic/inappropriate questions — open-domain chat risk (saying something dumb in
  front of an audience) is treated as a real product risk, not paranoia.

---

## 12. Full function reference — every function, every file

Every function/method in the codebase, what it takes, and exactly what it does. Private
helpers (leading underscore, or `static` in C++) are listed alongside public interface
functions since both matter for understanding the code. Grouped by file, in the order
each file defines them.

### `bus.py`

| Function | Signature | What it does |
|---|---|---|
| `speech_start` | `() -> Event` | Builds the `"speech_start"` event — fired the instant VAD or PTT detects the visitor starting to talk. |
| `partial` | `(text: str) -> Event` | Builds a `"partial"` event carrying an in-progress (not yet finalized) transcript, for debug/UI use only. |
| `final` | `(text: str, gender: str = "unknown") -> Event` | Builds a `"final"` event: the finished transcript for a turn plus a pitch-based gender guess used to pick the TTS voice. |
| `barge_in` | `() -> Event` | Builds the `"barge_in"` event: the visitor spoke while the bot was mid-reply; tells `main.py` to abort generation and playback. |
| `new_session` | `() -> Event` | Builds the `"new_session"` event: a quick PTT tap, meaning "treat this as a new visitor," not a spoken turn. |
| `Bus.__init__` | `(self)` | Creates the single underlying `asyncio.Queue` that every event flows through. |
| `Bus.publish` | `(self, event: Event) -> None` | Enqueues an event from code already running on the asyncio event loop. |
| `Bus.publish_threadsafe` | `(self, loop, event: Event) -> None` | Same as `publish`, but safe to call from a non-asyncio thread (used by the `sounddevice` audio callback thread) via `loop.call_soon_threadsafe`. |
| `Bus.get` | `(self) -> Event` (async) | Awaits and returns the next event off the queue; this is what `main.py`'s consumer loop calls in a `while True`. |

### `config.py`

| Function | Signature | What it does |
|---|---|---|
| `_collect_keys` | `(prefix: str, max_keys: int = 12) -> list[str]` | Reads `PREFIX`, `PREFIX_2`, `PREFIX_3`, ... `PREFIX_{max_keys}` from the environment (loaded from `.env`) and returns whichever are actually set, in order. This is how `GROQ_API_KEYS` and `TAVILY_API_KEYS` become lists of one-account-per-key for quota rotation; gaps (e.g. only `_2` and `_4` set) are fine. Everything else in the file is plain data/constants, not functions. |

### `nethealth.py`

| Function | Signature | What it does |
|---|---|---|
| `NetHealth.__init__` | `(self, config)` | Sets up the breaker in the "internet is fine" state (`ok = True`), stores a reference to `config` for the tunable probe constants, and initializes the (not-yet-started) background probe task handle. |
| `NetHealth.mark_down` | `(self) -> None` | Trips the breaker: sets `ok = False` (unless already down — idempotent) and, if no probe task is already running, starts `_probe_loop()` as a background asyncio task. Called by `brain.py` and `hearing.py`/`voice.py` whenever a cloud call fails with a network-shaped error. |
| `NetHealth._probe_loop` | `(self) -> None` (async) | Runs only while `ok` is `False`. Every `CLOUD_PROBE_INTERVAL_S` seconds, attempts a bare TCP connect to `CLOUD_PROBE_HOST:CLOUD_PROBE_PORT` (default `api.groq.com:443`) with a `CLOUD_PROBE_TIMEOUT_S` budget; success flips `ok` back to `True` and the loop exits (naturally, since its own `while not self.ok` condition then fails). Skips the actual probe (but still sleeps) while `simulate_offline` is set, for the REPL test hook. |
| `_default` | `() -> NetHealth` | Module-private factory that imports `config` and constructs the one shared `NetHealth` instance. |
| *(module level)* | `health = _default()` | The actual shared singleton every other module imports and reads/writes — not a function, but the entire point of the file. |

### `guests.py`

| Function | Signature | What it does |
|---|---|---|
| `Roster.__init__` | `(self, guests: list[Guest])` | Stores the guest list and precomputes each guest's name as a lowercase set of word-tokens (shared by both matchers below, computed once instead of per-call). |
| `Roster.match` | `(self, spoken_name: str) -> Guest \| None` | Matches an already-extracted, ≤2-word spoken name against the roster. For 2-word input, requires the guest's *entire* token set to be a subset of the spoken words (i.e. an exact multi-word match); for 1-word input, requires that single word to appear in exactly one guest's name. Returns `None` on zero or *ambiguous* (multiple) matches — a wrong VIP greeting is treated as worse than the generic fallback. |
| `Roster.match_in_text` | `(self, text: str) -> Guest \| None` | Scans a full raw transcript (not a pre-extracted name) for roster name tokens — handles VIPs introduced with a title attached, which the generic 2-word extractor would mangle. Requires ≥2 overlapping name tokens, or a full match for a single-token name, and again only returns a hit if exactly one guest qualifies. |
| `load` | `(path: str) -> Roster` | Reads and parses `special-gest.md` (or whatever path is given) into a `Roster` of `Guest` objects, splitting on blank lines and matching each block against `_ENTRY_RE` (`## Name` / `Title:` / `Background:` / `Contributions:`). Missing file or unparsable blocks are logged and skipped, never raise — an empty roster just means no VIP welcomes fire this run. |

### `hearing.py`

| Function | Signature | What it does |
|---|---|---|
| `Hearing.__init__` | `(self, bus: Bus, config)` | Loads the Silero VAD model (ONNX) and the Vosk `KaldiRecognizer` once; sets up the (optional) Groq Whisper client(s) for online STT if configured; initializes all VAD/turn-taking state, the bounded preroll/pitch/audio-frame ring buffers, and the bounded incoming-frame queue. |
| `Hearing.robot_speaking` (property) | getter `(self) -> bool` / setter `(self, value: bool)` | Getter returns whether TTS is currently playing. The setter, called by `main.py`, records the timestamp when playback starts (for the barge-in suppression window) and, on playback ending, arms a short "quiet until" window to absorb the speaker's reverb tail so it isn't mistaken for the next visitor speaking. |
| `Hearing._on_audio` | `(self, indata, _frames, _time, status) -> None` | The raw `sounddevice` callback, running on its own audio thread. Logs any stream `status` warning and hands a copy of the frame to the asyncio loop via `call_soon_threadsafe` — this is the only code allowed to touch anything outside the callback's own thread directly. |
| `Hearing._enqueue` | `(self, frame: np.ndarray) -> None` | Runs on the event loop (scheduled by `_on_audio`). Pushes the frame onto the bounded `asyncio.Queue`; if the queue is full, the frame is silently dropped rather than buffered forever. |
| `Hearing.start` | `(self) -> None` (async) | Opens the `sounddevice.InputStream` (16kHz, mono, int16, 512-sample blocks), starts the PTT keyboard listener, and loops forever pulling frames off the queue and handing each to `_process`, until `stop()` is called. |
| `Hearing.stop` | `(self) -> None` (async) | Signals the loop in `start()` to exit, closes the input stream, and stops the PTT keyboard listener. |
| `Hearing._start_ptt` | `(self) -> None` | If `INPUT_MODE` includes PTT, starts a `pynput` global keyboard listener for the spacebar (press → `_set_ptt(True)`, release → `_set_ptt(False)`), scheduled safely onto the event loop. If `pynput` isn't available, logs a warning and silently falls back to VAD-only. |
| `Hearing._set_ptt` | `(self, down: bool) -> None` | The debounced PTT press/release handler. On press while not already speaking: force-enters the speaking state, bypassing VAD, and records the press time. On release while speaking: if it was a short tap (`< PTT_TAP_MAX_MS`) that itself started the speaking state, treats it as a new-session signal (`_cancel_as_tap`); otherwise finalizes the turn normally. |
| `Hearing._vad_prob` | `(self, frame: np.ndarray) -> float \| None` | Returns Silero's speech probability for one frame, or `None` if PTT is currently overriding VAD entirely (either `INPUT_MODE == "ptt"` or the button is currently held) — `None` is the signal downstream code uses to skip VAD-based logic. |
| `Hearing._process` | `(self, frame: np.ndarray) -> None` | The per-frame state-machine core. While not speaking: buffers a small preroll, and (if VAD is active) counts consecutive above-threshold frames — using a ducked threshold and an echo-suppression window while the bot itself is talking — to decide when to call `_enter_speaking()`. While speaking: feeds the frame to pitch estimation and Vosk, accumulates silence duration using VAD hysteresis (or resets it if PTT is held, since PTT ends turns on release, not silence), and calls `_finalize()` once the required silence window is reached. |
| `Hearing._required_silence_ms` | `(self) -> float` | Looks at the last word seen in the partial (or last finalized) transcript; if it's in `config.INCOMPLETE_TAIL_WORDS` (an article, conjunction, question word, hesitation, etc.), returns the longer `VAD_SILENCE_HOLD_MS` instead of the normal `VAD_SILENCE_MS`, because a pause after such a word usually means the visitor is still mid-thought. |
| `Hearing._enter_speaking` | `(self) -> None` | Resets all per-turn counters/buffers, replays the buffered preroll frames into the pitch/audio buffers and into Vosk (so the very first syllable isn't lost to detection latency), immediately publishes `speech_start` (this is what makes the robot's head-turn feel instantaneous), and additionally publishes `barge_in` if the robot was mid-reply. |
| `Hearing._feed_vosk` | `(self, frame: np.ndarray) -> None` | Feeds one frame into the Kaldi recognizer. If it completes an internal Vosk utterance boundary, appends the finalized chunk of text and clears the "stale" partial. Otherwise, if the live partial result changed, publishes a `partial` event with the finalized-so-far text plus the new partial appended. |
| `Hearing._estimate_gender` | `(self) -> str` | Runs a per-window YIN pitch estimator (with a harmonic-correction step) over the turn's buffered audio, collects a median-ish (25th-percentile) fundamental frequency across confidently-voiced windows, and classifies `"male"`/`"female"` against `PITCH_MALE_MAX_HZ`, or returns `"unknown"` if too few windows were confidently voiced. |
| `Hearing._finalize` | `(self) -> None` | Ends the current turn: pulls Vosk's final result, resets the recognizer and VAD internal state (mandatory — otherwise state leaks into the next turn), assembles the full transcript text, estimates gender, snapshots the buffered raw audio (if the online-STT tier is enabled), then clears all per-turn buffers. Applies the junk/length filters (too short, too little speech, in `JUNK_SET`, or over `MAX_TRANSCRIPT_CHARS` → truncate) and either kicks off `_publish_online` (if online STT is enabled and the network is up) or publishes a plain `final` event immediately with the Vosk transcript. |
| `Hearing._cancel_as_tap` | `(self) -> None` | The short-PTT-tap path: performs the same recognizer/VAD/buffer reset as `_finalize`, but discards the transcript entirely and publishes `new_session` instead — signals "new visitor," not a spoken turn. |
| `Hearing._publish_online` | `(self, fallback_text: str, gender: str, pcm: bytes) -> None` (async) | Tries to beat the Vosk transcript with a Groq Whisper transcription of the turn's raw audio within `STT_ONLINE_TIMEOUT_S`; on success, uses the online text instead, on any failure/timeout logs a warning, trips the shared breaker, and keeps the Vosk fallback text. Either way, truncates to `MAX_TRANSCRIPT_CHARS` and publishes the `final` event — a turn always gets *some* transcript, never silence. |
| `Hearing._transcribe_online` | `(self, pcm: bytes) -> str` (async) | Wraps the raw int16 PCM into a proper WAV container in memory, then tries each configured Groq Whisper client in turn (key rotation on failure, same pattern as `brain.py`), returning the first successful transcription text; re-raises the last error if every key fails. |
| `_main` | `() -> None` (async, standalone test) | Boots a bare `Hearing` instance against a fresh `Bus` and prints every event as it arrives with a millisecond timestamp — the `python hearing.py` smoke test. |

### `brain.py`

| Function | Signature | What it does |
|---|---|---|
| `_needs_search` | `(text: str) -> bool` | Regex-checks whether a transcript looks like it needs a live web-search answer (time-sensitive words, "who is/won," named political titles, explicit "search"/"look up," etc.) — gates whether Tavily gets called at all, so ordinary chat never pays that latency. |
| `_norm` | `(text: str) -> str` | Lowercases and strips everything except word characters and spaces (keeping Unicode letters like Urdu/Devanagari) — used to normalize text before matching against the canned-answer keys. |
| `_extract_name` | `(text: str) -> str \| None` | Best-effort name extraction from a raw transcript: strips a leading hesitation ("uh, um, well, so…"), then a name lead-in phrase ("my name is," "I'm," "call me," …), keeps at most the first two remaining words, rejects if the first word is an obvious non-name stopword ("no," "yes," "hi," "sorry," …), and title-cases the result. Returns `None` on any doubt rather than guessing wrong. |
| `Brain.__init__` | `(self, bus: Bus, config)` | Stores config/bus, initializes empty conversation history and idle-timer state, and constructs one `AsyncGroq` client per configured Groq API key (so each key gets its own connection/quota tracking). |
| `Brain.simulate_offline` (property) | getter/setter | Proxies straight through to the shared `nethealth.health.simulate_offline` flag — lets the REPL's `/offline` command force *both* brain and voice offline via one shared switch. |
| `Brain.cloud_ok` (property) | getter `(self) -> bool` | Returns the shared breaker's current `ok` state. |
| `Brain.respond` | `(self, text: str) -> AsyncIterator[str]` (async generator) | The main per-turn entry point. Resets history first if the visitor has been idle past `IDLE_RESET_S`; clears the abort flag; builds the full message list (system prompt + trimmed history + this turn); appends the user's message to history; streams tokens from `_stream()` while collecting them; once done, appends the assembled reply to history (even if the turn was aborted mid-way — that's what the visitor actually heard) and hard-trims history to `HISTORY_TURNS * 2` messages. |
| `Brain.welcome_special_guest` | `(self, guest: Guest) -> AsyncIterator[str]` (async generator) | The VIP-arrival variant of `respond()`: no prior history is used, instead a one-off prompt is built from `SPECIAL_GUEST_PROMPT_TEMPLATE` filled in with the guest's name/title/background/contributions. Uses the same filler/cascade streaming machinery as `respond()` but with a larger token budget and search disabled (VIP titles otherwise spuriously trigger the search-hint regex). Replaces history afterward with just this one exchange. |
| `Brain._stream` | `(self, messages, user_text, reply_parts, max_tokens=None, allow_search=True) -> AsyncIterator[str]` (async generator) | The shared engine behind both `respond` and `welcome_special_guest`. Immediately yields a random filler phrase. Kicks off `_cascade()` as a background task and races its first token against `FILLER_CHAIN_S`-second windows using `asyncio.wait` (deliberately not `wait_for`, so the cascade keeps running underneath while more fillers are chained in, up to `FILLER_CHAIN_MAX` times, if the model is slow to answer). Once the first real token lands (or the turn is aborted), streams the rest of the cascade's tokens through, appending each to `reply_parts` for the caller to persist into history. |
| `Brain.abort` | `(self) -> None` | Sets the internal `asyncio.Event` that every streaming loop in `_stream`/`_cascade`/`_groq_stream`/`_ollama_stream` checks between chunks — this is what a barge-in calls to kill generation immediately. |
| `Brain.set_visitor_name` | `(self, name: str) -> None` | Records the current visitor's name outside of `_history` (so it survives history's hard trim). Threaded into the cloud path via the system prompt and into the Ollama path via the user message (see `_ollama_stream` — small models were verified to ignore facts injected into the system prompt mid-conversation). |
| `Brain._with_visitor_name` | `(self, base_prompt: str) -> str` | Appends a plain-language "The visitor's name is X" line to the given system prompt if a name is currently set; returns the prompt unchanged otherwise. |
| `Brain._ollama_system_prompt` | `(self) -> str` | Returns `config.OLLAMA_SYSTEM_PROMPT` if defined, else falls back to the normal cloud `SYSTEM_PROMPT` — lets a config file omit the Ollama-specific prompt without breaking anything. |
| `Brain.warm_ollama` | `(self) -> None` (async) | Fire-and-forget boot-time call: sends a throwaway 1-token chat request to Ollama with `keep_alive: "6h"` purely to force the model to load into memory and its system prompt to be evaluated into the prefix cache ahead of the first real (possibly mid-outage) request. Any failure is logged and ignored, never raised. |
| `Brain.reset` | `(self) -> None` | Clears conversation history and the stored visitor name — called on idle timeout or an explicit new-session (PTT tap). |
| `Brain._tavily_search` | `(self, query: str) -> str \| None` (async) | Tries each configured Tavily API key in turn, POSTing the query and parsing up to `TAVILY_MAX_RESULTS` results into a short context string the LLM is told to use naturally. Every failure (network or malformed response) is caught and logged, moving to the next key; returns `None` if every key fails or none are configured. Never raises, never touches the shared network breaker (a Tavily-specific failure isn't evidence Groq/the internet is down). |
| `Brain._attempts` | `(self) -> list[tuple[int, str]]` | Builds the ordered list of `(key_index, model)` pairs the cascade will try: every key against the primary Groq model first, then every key again against the fallback model — "rotate key before downgrading model." |
| `Brain._cascade` | `(self, messages, user_text, max_tokens=None, allow_search=True) -> AsyncIterator[str]` (async generator) | The actual provider fallback chain. If backend is `"groq"` and the breaker is up: optionally folds in a Tavily search result, then tries every `(key, model)` pair from `_attempts()` — a network-shaped error jumps straight to Ollama (rotating keys won't fix a dead connection) and trips the breaker; any other error just tries the next pair; if a partial reply already started streaming before the failure, it stops there rather than restarting and repeating itself. Falls through to `_ollama_stream` if backend allows it, and finally to `_canned` as the last resort that can never itself fail. |
| `Brain._groq_stream` | `(self, key_idx, model, messages, max_tokens=None) -> AsyncIterator[str]` (async generator) | Issues one streaming Groq chat-completion request (raising immediately if `simulate_offline` is set), logs the rate-limit headers from the response for visibility, then manually iterates the stream chunk-by-chunk with a per-token timeout (`GROQ_TOKEN_TIMEOUT_S`) so a stalled connection can't hang the turn — a timeout raises and (via `_cascade`) trips the breaker. Checks the abort flag between chunks and closes the stream early if set. |
| `Brain._ollama_stream` | `(self, messages, max_tokens=None) -> AsyncIterator[str]` (async generator) | Reshapes the conversation for the local model: a compact system prompt, only the last `OLLAMA_HISTORY_TURNS` turns, and the visitor's name plus a "reread the context" hint folded directly into the trailing user message (never the system prompt — see `set_visitor_name`'s docstring for why). Streams the response from Ollama's `/api/chat` endpoint line-by-line, parsing each JSON line and yielding its token content, stopping on the `done` flag or on abort. |
| `Brain._canned` | `(self, user_text: str) -> str` | The last-resort layer: normalizes the transcript and looks for a substring match against `config.CANNED`'s keys; if none, tries a fuzzy `difflib` close-match (cutoff 0.6); if still nothing, returns `CANNED_DEFAULT`. Can never fail to produce *some* text. |
| `_repl` | `() -> None` (async, standalone test) | The `python brain.py` interactive test: reads lines from stdin, streams the reply to stdout with the filler shown separately, and prints which provider actually answered plus time-to-first-token. Implements `/offline` and `/online` commands that flip `brain.simulate_offline` to test the fallback cascade live without actually pulling the network cable. |

### `voice.py`

| Function | Signature | What it does |
|---|---|---|
| `_filler_key` | `(text: str) -> str` | Normalizes a filler phrase (strip whitespace/punctuation, lowercase) into the lookup key used by both the local and online filler PCM caches. |
| `Voice.__init__` | `(self, bus, mqtt, config)` | Loads one `PiperVoice` per configured gender (always available, offline); records the configured online neural-voice names per gender (empty dict = that tier disabled); pre-synthesizes every filler phrase to raw PCM for every local voice (`_filler_cache`) — this both makes fillers instant later and warms up Piper's ONNX sessions so the first *real* clause of the whole run isn't the one paying the cold-start cost; opens the persistent `sounddevice.OutputStream` sized so one audio callback == exactly one amp frame at `AMP_HZ`. |
| `Voice._on_audio_block` | `(self, outdata, frames, _time, _status) -> None` | The playback stream's callback (runs on its own audio thread). Pulls up to `frames` worth of pending PCM out of the shared buffer under a lock, writes it (zero-padding any shortfall) into `outdata`, and — if any real audio was output — computes RMS → clipped/scaled 0–255 amplitude → EMA-smoothed against the previous value → publishes it as the `amp` MQTT message. If the buffer just ran dry, force-publishes `amp:0` once so the jaw can't stay stuck open. |
| `Voice._synth_with` | `(self, voice: PiperVoice, text: str) -> bytes` | Runs Piper synthesis for one voice/text pair and concatenates the resulting int16 PCM chunks into one byte string. |
| `Voice._synth` | `(self, text: str) -> bytes` | Convenience wrapper: synthesizes with whichever Piper voice matches the currently-selected gender. |
| `Voice._decode_mp3` | `(self, mp3: bytes) -> bytes` | Shells out to `ffmpeg` (via `subprocess.run`) to decode edge-tts's mp3 output into raw int16 PCM at Piper's sample rate, so the rest of the pipeline never needs to know which tier produced the audio. Raises if ffmpeg produced nothing. |
| `Voice._synth_online_with` | `(self, voice_name: str, text: str) -> bytes` (async) | Streams mp3 audio chunks from `edge_tts.Communicate(...).stream()`, concatenates them, then decodes via `_decode_mp3` in a thread-pool executor (since `ffmpeg` invocation is blocking). Raises if edge-tts returned nothing. |
| `Voice.warm_online` | `(self) -> None` (async) | Fire-and-forget boot-time call: if online voices are configured and the network is currently up, pre-synthesizes every filler phrase in every online voice, in parallel, and populates the online filler cache. Best-effort — a failure here just means online turns temporarily borrow the Piper filler until it succeeds. |
| `Voice._enqueue` | `(self, pcm: bytes) -> None` | Appends synthesized PCM to the shared playback buffer under the lock (this is what `_on_audio_block` drains). |
| `Voice._speak_clause` | `(self, clause: str) -> None` (async) | Extracts and publishes any `[gesture]` tags from the clause as MQTT `gest` messages, then strips them from the text before synthesis. Prefers a cached filler clip (matching tier if online is active, else the Piper one) if the clause *is* a filler; otherwise tries online synthesis (falling back to local on failure/timeout, tripping the breaker), and finally local Piper synthesis if nothing else produced audio. Enqueues the result unless the turn has been aborted in the meantime. |
| `Voice.set_gender` | `(self, gender: str) -> None` | Updates the active voice gender from a pitch-based guess, but only accepts the *first* confident detection per conversation (locks afterward) to avoid mid-conversation flip-flopping from noisy per-utterance pitch estimates; a gap longer than `IDLE_RESET_S` since the last update unlocks it (presumed new visitor). `"unknown"` guesses are always ignored. |
| `Voice.reset_gender` | `(self) -> None` | Explicit session-boundary reset (called on a PTT-tap new session): drops back to the configured default gender and unlocks, same underlying fields the timeout path uses. |
| `Voice.speak_stream` | `(self, tokens: AsyncIterator[str]) -> None` (async) | The main entry point, called once per turn. Publishes `state:"speak"`, then buffers incoming tokens and flushes to `_speak_clause` whenever the buffered text ends on a sentence boundary character *and* is long enough (or is itself a filler, which bypasses the length check so it plays instantly). Flushes any remainder at stream end, waits for the playback buffer to fully drain (`_drain`), and force-publishes `amp:0` as a final belt-and-braces close. |
| `Voice.kill` | `(self) -> None` | Barge-in: sets the abort flag, clears the pending PCM buffer under the lock (without this, already-buffered audio would resume audibly a beat later), zeroes the amplitude, and publishes `state:"attend"`. Cuts mid-word by design. |
| `Voice.close` | `(self) -> None` | Stops and closes the persistent output audio stream — called on process shutdown. |
| `Voice._drain` | `(self) -> None` (async) | Polls the pending-PCM buffer length every 20ms until it's empty or the turn is aborted — lets `speak_stream` know playback has actually finished, not just that synthesis has. |
| `_tokens_at_once` | `(text: str) -> AsyncIterator[str]` (async generator, standalone test) | Yields the entire given text as one single token — simulates a non-streaming caller for the `python voice.py "text"` test. |
| `_tokens_slow` | `(text: str, delay: float = 0.05) -> AsyncIterator[str]` (async generator, standalone test) | Yields the text one word at a time with a delay between each — simulates a slow LLM stream, used by the `--slow` test flag to demonstrate clause chunking starting playback before the full reply exists. |
| `_main` | `() -> None` (async, standalone test) | The `python voice.py [--slow] "text"` entry point: boots a `Voice` against a real `Robot`, times each stage (boot, clause-queued, drained), and prints a timing trace; handles Ctrl-C by calling `kill()` and reporting the cut. |

### `robot.py`

| Function | Signature | What it does |
|---|---|---|
| `Robot.__init__` | `(self, host=..., port=..., topic=..., qos=...)` | Constructs a `paho.mqtt.client.Client` (callback API v2), calls `connect_async` + `loop_start` — this never blocks and never raises even if no broker is reachable; the client will keep retrying in the background on its own thread. |
| `Robot._publish` | `(self, payload: dict) -> None` | JSON-serializes and publishes the payload to the configured topic/QoS, wrapped in a bare `try/except: pass` — any failure (broker down, disconnected) is a silent no-op by design; the robot body is optional, the conversation is not. |
| `Robot.state` | `(self, v: str) -> None` | Publishes `{"t": "state", "v": v}` — one of `idle`/`attend`/`thinking`/`speak`. |
| `Robot.amp` | `(self, v: int) -> None` | Publishes `{"t": "amp", "v": v}` — mouth amplitude, 0–255, expected at ~40Hz during speech, and always sent as 0 on stop. |
| `Robot.gest` | `(self, v: str) -> None` | Publishes `{"t": "gest", "v": v}` — one of `nod`/`tilt`/`shrug`. |
| `Robot.close` | `(self) -> None` | Stops the MQTT network loop thread and disconnects cleanly — called on process shutdown. |

### `main.py`

| Function | Signature | What it does |
|---|---|---|
| `Rebotic.__init__` | `(self, latency: bool = False)` | Constructs the bus, `Robot`, `Hearing`, `Brain`, `Voice`, and loads the guest roster; initializes session/latency-tracking state. If `--latency` is enabled, wraps `robot.amp` so the exact moment audio first becomes non-silent is timestamped for the per-turn report. |
| `Rebotic.run` | `(self) -> None` (async) | The top-level boot/run/shutdown sequence: installs a SIGINT handler, fires the `warm_ollama`/`warm_online` background tasks, plays the greeting (before the mic opens, so the robot can't hear/barge itself in on its own hello), starts `hearing.start()` and the `_consume()` event loop, then waits for the stop signal. On shutdown: cancels the consumer, aborts/kills any in-flight brain/voice work, stops hearing, closes voice's audio stream, zeros the robot's amplitude, sets it idle, and disconnects MQTT. |
| `Rebotic._consume` | `(self) -> None` (async) | The main event dispatch loop: pulls events off the bus forever and reacts per `Event.type` — `speech_start` → attend; `partial` → debug log only; `barge_in` → `_interrupt()`; `new_session` → `_start_new_session()`; `final` → updates the voice's locked gender, prints the transcript, safety-interrupts any still-running speak task, then routes to either `_handle_name_reply` (if awaiting a visitor's name) or `_turn` (normal conversation turn) as a new background task. |
| `Rebotic._say` (staticmethod) | `(text: str) -> AsyncIterator[str]` (async generator) | Wraps a single fixed string as a one-item async token stream, so fixed lines (greeting, session prompts) can be fed through the exact same `voice.speak_stream` machinery as LLM-generated streams. |
| `Rebotic._interrupt` | `(self) -> None` | The barge-in handler: marks the turn as interrupted, calls `brain.abort()` and `voice.kill()` (which cuts audio mid-word, zeroes amp, and sets state to attend). |
| `Rebotic._start_new_session` | `(self) -> None` | Handles a PTT-tap `new_session` event: interrupts anything currently playing, resets the brain's history and the voice's locked gender, and starts asking the visitor for their name (`SESSION_NAME_PROMPT`). |
| `Rebotic._handle_name_reply` | `(self, text: str) -> None` (async) | Handles the transcript that comes back while awaiting a name. Checks the *full raw transcript* against the VIP roster first (`guests.match_in_text`) — VIPs often self-introduce with a title attached, which the generic extractor would mangle. A VIP hit triggers the special ceremonial welcome. Otherwise runs the generic `_extract_name`; a successful extraction sets the visitor's name and acknowledges it; a first failed attempt asks again once; a second failed attempt gives up asking and just answers whatever the visitor actually said, rather than leaving them met with silence. |
| `Rebotic._speak` | `(self, tokens: AsyncIterator[str]) -> None` (async) | The shared THINK→SPEAK→RESET sequence used by `_turn`, `_say_line`, and the VIP welcome path alike: publishes `state:"thinking"`, ducks VAD and arms barge-in (`hearing.robot_speaking = True`) for the duration of `voice.speak_stream(tokens)`, then (unless the turn was interrupted, which already set state to `attend` via `voice.kill()`) publishes `state:"idle"`. |
| `Rebotic._say_line` | `(self, text: str) -> None` (async) | Convenience wrapper: runs `_speak` on a fixed, non-LLM-generated line (used for the session-prompt/ack lines). |
| `Rebotic._turn` | `(self, text: str) -> None` (async) | One full normal conversation turn: records the latency start timestamp, runs `_speak` over `brain.respond(text)` (tapped through `_tap` for latency instrumentation), and prints the latency breakdown if `--latency` is enabled. |
| `Rebotic._tap` | `(self, tokens: AsyncIterator[str]) -> AsyncIterator[str]` (async generator) | A pass-through wrapper around the brain's token stream that timestamps the first *real* LLM token (explicitly skipping the filler, which is always the first yielded item) into `self._t_first_token`, for the `--latency` report. |
| `Rebotic._report_latency` | `(self, t_done: float) -> None` | Formats and prints the per-turn latency breakdown: fixed VAD silence window, time to first LLM token, time to first audible audio, time to full completion, and the headline `speech_end→first_audio` number checked against the sub-1000ms target — or a note that no audio played if the turn was aborted before any. |
| `_main` | `() -> None` (async) | Parses `--latency`/`--debug` CLI flags, configures logging, constructs and runs the `Rebotic` app — the actual `python main.py` entry point. |

### Firmware — `body/Rebotic/Rebotic.ino`

| Function | Signature | What it does |
|---|---|---|
| `setup` | `()` | Arduino entry point, runs once at boot: starts `Serial` (115200 baud), configures and initializes the hardware watchdog (`esp_task_wdt`, 8s timeout, retargeting an already-initialized watchdog if one exists), subscribes the current (loop) task to it, then calls `netBegin(armsOnCommand)` (connects WiFi/MQTT, wires `armsOnCommand` as the command handler) followed by `armsBegin()` (attaches and eases all 6 servos to their neutral pose). |
| `loop` | `()` | Arduino main loop, runs continuously: pets the watchdog, services WiFi/MQTT (`netLoop()`), checks whether no command has arrived in `NO_CMD_TIMEOUT_MS` (10s) — if so, forces the arm state to `idle` exactly once (guards against repeatedly re-forcing every iteration) on the theory the laptop probably restarted or the link dropped — and finally calls `armsTick(now)` to advance whatever animation/gesture is currently in progress. |

### Firmware — `body/Rebotic/net.h` / `net.cpp` (WiFi + MQTT transport)

| Function | Signature | What it does |
|---|---|---|
| `resolveBrokerIp` (static) | `(uint32_t timeoutMs) -> bool` | Queries mDNS for `MQTT_BROKER_MDNS_NAME` (the laptop's hostname, `"aiwix"`) with the given timeout; on success, caches the resolved `IPAddress` and logs it; returns whether resolution succeeded. |
| `mqttCallback` (static) | `(char* topic, byte* payload, unsigned int length)` | `PubSubClient`'s message-received hook. Parses the payload as JSON (`ArduinoJson`), silently ignores malformed JSON or a payload missing either the `t` or `v` field, otherwise records the current time as the last-command timestamp and forwards `{t, v}` to whatever handler was registered via `netBegin`. |
| `wifiConnectBlocking` (static) | `()` | Called once from `netBegin`. First samples the BOOT button (GPIO0) over a 2.5-second window right at boot — if held the whole time, wipes the saved WiFi network via `WiFiManager::resetSettings()` so a human can force the setup portal to reopen without re-flashing. Then runs `WiFiManager::autoConnect(...)`, which either joins the previously-saved network or opens the `Rebotic-Setup` captive-portal AP and blocks until someone configures it (or it times out, at which point the ESP32 reboots to retry). Once connected, resets the WiFi backoff timer, starts the ESP32's own mDNS responder (`rebotic-arms.local`), does a best-effort broker IP resolution, and records the connect timestamp. |
| `wifiServiceNonBlocking` (static) | `(unsigned long now)` | Called every `netLoop()` iteration. If already connected, just updates the "last connected" timestamp and re-arms the auto-portal trigger. If disconnected for longer than `WIFI_AUTO_PORTAL_MS` (3 minutes), proactively opens the setup portal itself rather than waiting for a manual BOOT-button press (reasoning: nothing useful can happen without WiFi anyway, so blocking here costs nothing) — invalidates the cached broker IP afterward since the network may have changed. Otherwise, on an exponential backoff schedule (`WIFI_RETRY_MIN_MS` up to `WIFI_RETRY_MAX_MS`), calls a non-blocking `WiFi.reconnect()` to rejoin the already-saved network. |
| `mqttServiceNonBlocking` (static) | `(unsigned long now)` | Called every `netLoop()` iteration. Does nothing if WiFi isn't up. If already connected to the broker, just services `PubSubClient`'s internal `loop()` (which is what actually delivers subscribed messages to `mqttCallback`). Otherwise, on a throttled retry schedule, re-resolves the broker's mDNS IP if needed, then attempts to connect and (re-)subscribe to `rebotic/cmd`; a connect failure invalidates the cached broker IP so the next attempt re-resolves it (covers a DHCP lease change on the laptop). |
| `netBegin` | `(NetCommandHandler handler)` | Public entry point, called once from `setup()`. Stores the caller's command handler, performs the (blocking, first-time-only) WiFi connect, wires up the MQTT callback and a 256-byte buffer size, and stamps the last-command time so boot doesn't immediately look like a stale/dead connection. |
| `netLoop` | `()` | Public per-`loop()`-iteration entry point: services WiFi then MQTT via the two non-blocking functions above. Cheap when everything is already connected. |
| `netLastCmdMillis` | `() -> unsigned long` | Returns the `millis()` timestamp of the last successfully parsed `rebotic/cmd` message — used by `Rebotic.ino`'s "no command for 10s → force idle" rule. |
| `netIsConnected` | `() -> bool` | Returns whether both WiFi and the MQTT client are currently connected. |

### Firmware — `body/Rebotic/arms.h` / `arms.cpp` (6-servo gesture/animation engine)

| Function | Signature | What it does |
|---|---|---|
| `easeInOutCubic` (static) | `(float t) -> float` | Standard cubic ease-in-out curve (`t` in `[0,1]`) — the default easing for idle sway, state transitions, and gesture return-to-rest motion. |
| `easeOutBack` (static) | `(float t) -> float` | An "overshoot-then-settle" easing curve, used only for a gesture's *arrival* at its target pose (e.g. the shrug's lift) so it reads as a deliberate motion rather than a servo snapping to a setpoint. |
| `poseFor` (static) | `(ArmState s) -> Pose` | Returns the target angle offsets (lift/rotate/elbow deltas from each joint's calibrated base angle) for a given high-level state — currently only `STATE_ATTEND` has a distinct pose (arms lift, elbow relaxes open); every other state uses the neutral rest pose. |
| `swayFor` (static) | `(ArmState s) -> Sway` | Returns the idle "breathing" sway amplitude/frequency for shoulders and elbow for a given state — a slightly larger, faster "ambient talk-sway" while `STATE_SPEAK`, a smaller/slower one otherwise (covers idle/attend/thinking). |
| `statePoseAngle` (static) | `(int i) -> float` | Combines a joint's calibrated base angle with the current state's pose delta (selected by that joint's role: shoulder-lift/shoulder-rotate/elbow) to produce its target resting angle. |
| `breatheFor` (static) | `(int i, unsigned long now) -> float` | Computes the current sinusoidal "breathe" offset for one joint from the current state's sway parameters and that joint's own phase offset (so joints don't breathe in perfect lockstep — deliberately out of phase per joint). |
| `writeJoint` (static) | `(Joint &j, float angle)` | Applies a joint's mirror flag (right-side servos are physically mirrored: `180 - angle`), clamps to that joint's calibrated safe `[minAngle, maxAngle]` range, and issues the actual `Servo.write()` call — the single choke point every angle write passes through, so mirroring/clamping can never be skipped by accident. |
| `attachAll` (static) | `()` | Attaches all 6 servo objects to their GPIO pins (enables PWM output) and marks the joints as attached. |
| `detachAll` (static) | `()` | Detaches all 6 servos (stops PWM output, relieves holding current/heat) and marks them as unattached — called after `SERVO_DETACH_IDLE_MS` of no motion demand. |
| `armsTickGesture` (static) | `(unsigned long now) -> bool` | Advances the in-flight `shrug` gesture by one tick. For each joint, computes its own staggered elapsed time (shoulder leads; rotate and elbow trail by their configured offsets) through four phases — anticipation dip, ease-out-back strike to the shrug pose, a hold, then an ease-in-out return to the pose the gesture started from — blends in that joint's ongoing breathe offset, and writes the result. Returns whether *any* joint is still mid-gesture (the caller uses this to know when the gesture has fully finished). |
| `armsBegin` | `()` | Public boot function, called once from `setup()` after `netBegin()`. Attaches all servos, then blocking-eases every joint from the SG90's known power-on center (90°) to its calibrated resting pose over ~1 second (the only safe way to move from an *unknown* physical starting angle without a jump), then initializes the state machine into `STATE_IDLE`. |
| `armsTick` | `(unsigned long now)` | Public per-`loop()`-iteration function. First enforces the idle-detach safety timer (detaches all servos if no motion demand for `SERVO_DETACH_IDLE_MS` and no gesture is active; re-attaches on the next demand). If a gesture is active, advances it via `armsTickGesture` and clears the gesture-active flag once it reports finished. Otherwise, advances the current state's eased transition (with the same per-joint stagger concept, scaled down for faster response) toward that state's target pose, blended with the ongoing breathe sway, for every joint. |
| `armsOnCommand` | `(const char* t, const char* v)` | Public MQTT command handler, registered with `netBegin`. Refreshes the "last motion demand" timestamp on any recognized command (keeps servos attached). For `t == "state"`: maps the string value to an `ArmState` enum (unknown values ignored silently) and, if it's actually a change (or servos aren't currently attached), starts a new eased transition from each joint's current angle. For `t == "gest"`: only `"shrug"` is implemented (nod/tilt are neck-only, not built on this board) — starts the gesture, rolling fresh random amplitude/duration jitter (±10%/±15%) for this one instance so no two shrugs look identical, and snapshots each joint's current angle as the gesture's starting point. `t == "amp"` and any other unrecognized `t`/`v` are silently ignored (no jaw exists on this board; unknown values must never crash old firmware talking to a newer laptop protocol). |

---

## 13. Glossary / acronym reference

| Term | Meaning in this project |
|---|---|
| VAD | Voice Activity Detection (Silero VAD) — decides when someone is speaking |
| STT | Speech-to-Text (Vosk offline, Groq Whisper online) |
| TTS | Text-to-Speech (Piper offline, Microsoft edge-tts online) |
| TTFT | Time To First Token — the LLM latency metric the whole filler/streaming design exists to hide |
| PTT | Push-To-Talk — spacebar (dev) / future hardware button; primary input at an expo |
| RPD / RPM / TPM / TPD | Groq's rate-limit dimensions: Requests/Tokens Per Day/Minute — RPD is called out as the real ceiling for a multi-hour demo |
| Barge-in | Visitor interrupting the robot mid-reply; must cut audio instantly and abort generation |
| Filler | A short cached phrase ("hmm…") played the instant a turn ends, masking LLM latency |
| Breaker (circuit breaker) | `nethealth.py`'s shared online/offline flag, tripped by any cloud call failure |
| Gesture tag | `[nod]`/`[tilt]`/`[shrug]` inline in LLM output, stripped before speech, sent to the robot body |
| Clause chunking | Splitting the LLM token stream into whole-sentence chunks for TTS, enabling speak-while-generating |
| Amp / mouth-sync | RMS-derived 0-255 value published at 40Hz, driving the robot's jaw servo (spec) in time with audio |
