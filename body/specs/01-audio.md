# Spec 01 — AUDIO (ears and mouth live in the body)

**Scope:** The robot hears and speaks *from its body*, not from the laptop.
**Method:** Wired USB — a USB microphone and a powered speaker mounted inside
the body, cabled to the laptop. The laptop stays the mind: STT, LLM, and TTS
all still run there.
**Golden rule:** ZERO changes to the software tree. This entire spec is
hardware + OS configuration.

---

## Why zero code changes work

`hearing.py` opens `sounddevice.InputStream` and `voice.py` opens
`sounddevice.OutputStream` **without naming a device** — they use the OS
default source/sink. PulseAudio/PipeWire resamples transparently, so the
existing rates (16000 capture / 22050 playback) keep working on any USB
hardware. Re-pointing the OS defaults at the body's devices reroutes every
sample through the body; the Python code cannot tell anything changed.

---

## Parts

| Part | Requirement | Notes |
|---|---|---|
| USB microphone | compact, **directional/cardioid if possible**, USB-A | mini gooseneck or lavalier-style; mounts in the head or upper chest, facing the visitor |
| Powered speaker | USB-powered (or 3.5mm + USB power), small full-range driver | mounts behind a grille in the chest; 3–5W is plenty for one visitor at arm's length |
| USB extension cable ×2 | length to reach the laptop on its stand | route down the neck/back of the body |
| Acoustic foam scrap | — | isolation pad between speaker and mic mounts |

The **wired headset mic stays on the ship list** as the expo fallback input —
if the hall is too loud for the body mic, swap the OS default source back to
the headset in one command. Nothing else changes.

---

## OS routing (the actual "install")

Find the devices, then make them the defaults:

```bash
pactl list short sources      # find the body mic  (e.g. alsa_input.usb-...)
pactl list short sinks        # find the body speaker (e.g. alsa_output.usb-...)

pactl set-default-source <body-mic-name>
pactl set-default-sink   <body-speaker-name>
```

- Works identically on PipeWire (pactl talks to pipewire-pulse).
- Defaults persist across replugs of the *same* device — set once per machine.
- Streams already open keep their old device: set defaults **before**
  launching `main.py` (bring-up order in [04-bringup.md](04-bringup.md)).
- Rollback = same two commands pointing back at the laptop/headset devices.

---

## Mounting & acoustics

- **Mic faces forward**, at visitor mouth height, at the front of the head or
  chest. Nothing between mic and visitor.
- **Speaker angled down/away** from the mic — 90°+ off the mic axis, with the
  foam pad between their mountings. They share one small body; without this
  the robot hears itself loudly.
- Speaker behind a grille or fabric — protects the driver, looks intentional.
- USB runs leave the body through one strain-relieved exit at the back, then
  along the stand to the laptop. Visitors will tug things; cables must not be
  the thing that ends the demo.

---

## Echo & barge-in reality check

Mic and speaker co-located in one body is the *worst* case for the bot
hearing itself (already confirmed live with the open laptop mic on
2026-07-19). The existing mitigations carry over unchanged:

- `VAD_BARGE_IN = False` stays — VAD never triggers while the bot speaks.
- **PTT stays the primary input** (button on the body, spacebar for dev).
- `BARGE_IN_SUPPRESS_MS` reverb-tail guard stays.
- Re-enable `VAD_BARGE_IN` only if a test with the directional mic + foam
  isolation shows the bot cannot hear itself at demo volume. Test, don't hope.

(These are config values that already exist — no change is *required*;
flipping `VAD_BARGE_IN` after a passing echo test is a one-line config edit
the user makes deliberately.)

---

## Passes when (existing standalone tests, untouched)

```bash
python voice.py "Hello from my own chest"   # audio comes out of the BODY
python hearing.py                            # speak at the BODY → final transcript
python main.py --latency                     # full loop through body audio
```

- Voice is audible and clear from ~1.5m in front of the robot
- `hearing.py` transcribes speech aimed at the *robot* (not at the laptop)
- Echo check: run `main.py` at demo volume — the bot must not barge itself in
  during its own replies
- `--latency` still reports speech_end→first_audio < 1000ms (USB audio adds
  ~10–20ms; if latency regressed, the OS picked a resampling path — check
  `pactl` device sample rates)

---

## Failure modes

| Symptom | Cause |
|---|---|
| Sound still from laptop speakers | Defaults set after `main.py` started, or set on the wrong sink |
| Transcripts got worse | Omni mic picking up the room — swap to the headset fallback |
| Bot interrupts itself | Speaker too close/loud for the mic — re-aim, add foam, lower volume |
| Crackling / dropouts | USB hub power starvation — plug mic and speaker into separate laptop ports |
| Everything dead after replug | Different USB device enumerated — re-run the two `pactl` commands |
