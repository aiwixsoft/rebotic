# Rebotic Body — Hardware Subproject

The physical robot. **Completely separate from the software codebase** in the
repo root — nothing here ever requires editing a `.py` file up there.

The split, in one line: **the laptop is the mind, this directory is the body,
and they talk over a wire (USB audio) and the air (MQTT intents).**

---

## Division of labor

| | Mind (repo root, laptop) | Body (this directory) |
|---|---|---|
| Hearing/thinking/voice synthesis | specs 01–03, Python | — |
| Microphone (the *ears*) | opens OS default input | USB mic **mounted in the body**, cabled to laptop |
| Speaker (the *mouth sound*) | opens OS default output | powered speaker **mounted in the body**, cabled to laptop |
| Motion, eyes, jaw (the *face*) | publishes MQTT intent | ESP32 + servos + LEDs |
| Push-to-talk (the *hand raise*) | consumes `rebotic/evt` | arcade button on ESP32 |

Why zero software changes: `hearing.py` and `voice.py` open the OS **default**
audio devices. Making the body's USB mic and speaker the OS defaults reroutes
all audio through the body — the code never knows anything moved
([specs/01-audio.md](specs/01-audio.md)).

---

## Specs

- [`specs/01-audio.md`](specs/01-audio.md) — USB mic + powered speaker in the body, OS-level routing
- [`specs/02-firmware.md`](specs/02-firmware.md) — ESP32 firmware: intent → motion/light, PTT → laptop
- [`specs/03-electronics.md`](specs/03-electronics.md) — BOM, pinout, wiring, power budget
- [`specs/04-bringup.md`](specs/04-bringup.md) — staged bench → integration → soak procedure

Software-side context (architecture, MQTT contract, expo rules):
[`../.claude/PROJECT.md`](../.claude/PROJECT.md)

---

## MQTT contract (summary — authority is ../.claude/PROJECT.md)

```json
laptop → body   topic rebotic/cmd:
  {"t":"state","v":"idle|attend|thinking|speak"}
  {"t":"amp","v":0-255}              // 40Hz during speech
  {"t":"gest","v":"nod|tilt|shrug"}

body → laptop   topic rebotic/evt:
  {"t":"ptt","v":"down|up"}          // hardware push-to-talk
```

Intent only, never servo angles. The body owns all timing and easing.

---

## Future layout (documented, not yet created)

```
body/
├── PROJECT.md
├── specs/01..04
└── firmware/rebotic_body/    # PlatformIO project — NOT started; specs first
```

The laptop-side glue for `rebotic/evt` (hardware PTT) is the one future change
that will touch the software tree — it's called out in specs/02-firmware.md
and stays unwritten until explicitly requested.
