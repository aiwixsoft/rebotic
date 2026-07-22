# Spec 03 — ELECTRONICS (BOM · wiring · power)

**Scope:** Everything physical between the laptop and the moving, hearing,
speaking robot.
**Rule zero:** External 5V for servos, **common ground** with the ESP32, or
nothing else in this spec matters (the ESP32's regulator browns out under
servo stall current and you'll debug "random" reboots).

---

## Bill of materials

| # | Part | Qty | Role | Notes |
|---|---|---|---|---|
| 1 | ESP32 DevKit v1 (30-pin) | 1 | body controller | any WROOM-32 board works |
| 2 | SG90 micro servo | 5 (+2 spare) | jaw, neck pan, head tilt, left arm, right arm | spares are on the ship list |
| 3 | WS2812B ring/stick, 8px | 2 | eyes | single data line, chained |
| 4 | Arcade button (NO, momentary) | 1 | push-to-talk | big — visitors will mash it |
| 5 | **USB microphone** (directional) | 1 | the robot's ears | mounted in head/chest → laptop USB ([01-audio.md](01-audio.md)) |
| 6 | **Powered speaker** (USB or 3.5mm+USB power) | 1 | the robot's voice | behind chest grille → laptop ([01-audio.md](01-audio.md)) |
| 7 | **USB extension cable** | 2 | audio umbilicals | body → laptop, strain-relieved |
| 8 | 5V ≥5A supply (mains) | 1 | servo rail | 5× SG90 stall ≈ 3.3A; headroom matters |
| 9 | 1000µF electrolytic, ≥6.3V | 1 | across servo rail | kills stall-current dips |
| 10 | 470Ω resistor | 1 | in series with WS2812B data | protects first pixel |
| 11 | USB cable, data | 1 | flash + power ESP32 from laptop | also the dev umbilical |
| 12 | Jumper wires + small breadboard/protoboard | — | wiring | spare jumpers on ship list |
| 13 | Pan/tilt bracket (SG90 size) + jaw horn/linkage | 1 | mechanics | printed or kit bracket |
| 14 | Acoustic foam scrap | — | mic/speaker isolation | [01-audio.md](01-audio.md) |

Mic and speaker are **laptop USB devices that happen to live in the body** —
they never connect to the ESP32. The wired headset mic stays on the ship list
as the loud-hall fallback input.

---

## Pinout (ESP32 — motion/eyes/button only)

| GPIO | Function | Why this pin |
|---|---|---|
| 13 | jaw servo signal | safe PWM pin |
| 14 | neck pan servo signal | safe PWM pin |
| 27 | head tilt servo signal | safe PWM pin |
| 26 | left arm servo signal | safe PWM pin |
| 33 | right arm servo signal | safe PWM pin |
| 25 | WS2812B data (eyes, chained) | safe output |
| 32 | PTT button → GND, `INPUT_PULLUP` | input-capable, no strapping role |
| 2 | onboard LED = MQTT-connected indicator | free debug signal |

**Avoid GPIO 0, 2 (as input), 12, 15** for servos/button — they're strapping
pins; a servo line holding one high at reset = boot loop that looks like
broken firmware. **GPIO 34–39 are input-only** — fine for buttons, never for
servos.

---

## Wiring

```
 5V 5A supply ──+──────────┬─────────┬──────────┬───────────┬────────────┐
                │          │         │          │           │            │
                │       jaw V+    pan V+     tilt V+    L-arm V+     R-arm V+    eyes V+ ─┘(same rail)
   1000µF ══════╡
                │
 supply GND ────+──── ESP32 GND ──── all servo GND ──── eyes GND ──── button ─→ GPIO32
                          │
 Laptop USB ──────────────┘  (powers ESP32 logic; supply stays separate)

 GPIO13 ─── jaw signal        GPIO26 ─── left arm signal    GPIO25 ──470Ω── eyes DIN
 GPIO14 ─── neck pan signal   GPIO33 ─── right arm signal
 GPIO27 ─── head tilt signal

 Body shell ──USB mic──────→ laptop USB port A      (audio: no ESP32 contact,
 Body shell ──USB speaker──→ laptop USB port B       separate ports — see below)
```

- Servo **signal** wires to ESP32, servo **power** wires to the external rail.
  Never bridge the supply's 5V to the ESP32 5V/VIN pin while USB is attached.
- The 1000µF cap sits physically at the servo power distribution point.
- Eyes (2×8 WS2812B @ full white) ≈ 1A worst case — the same 5V rail feeds
  them; firmware caps brightness at ~50% so the budget holds.
- **Mic and speaker into separate laptop USB ports**, not one bus-powered hub —
  a starved hub makes USB audio crackle.

---

## Power budget

| Load | Worst case | Rail |
|---|---|---|
| 5× SG90 stall | ~3.3A | external 5V supply |
| 16× WS2812B @ 50% | ~0.5A | external 5V supply |
| ESP32 (WiFi tx bursts) | ~0.5A | laptop USB |
| USB mic + powered speaker | ~0.5–1A | laptop USB (separate ports) |
| **External rail total** | **~3.8A → 5A supply is the floor, not comfort** | |

Firmware never stalls all five at once by design: gestures use arms OR neck,
and the jaw is the only servo moving continuously — worst case in practice is
well under the stall figure. The 5A floor covers the pathological case anyway.

Expo runs off mains (laptop too — battery mode downclocks). The power bank on
the ship list is the backup rail, not the plan.

---

## Mechanical notes

- Jaw: servo horn + stiff wire link to the lower jaw. 30° of servo travel
  should fully open the mouth — set the linkage so `JAW_OPEN`=40°
  ([02-firmware.md](02-firmware.md)) is *not* touching the mechanical stop.
- Pan/tilt: standard SG90 bracket, head mass as low as possible — foam-core
  head, not plywood. SG90s have plastic gears; the spares exist because of
  exactly this.
- Arms: one servo per shoulder, arm mounted directly on the horn — the servo
  shaft IS the shoulder joint. No elbow/wrist. Arms in foam/thin plastic:
  the servo holds them against gravity, so every gram costs lifetime.
- Mic/speaker mounting and acoustics: [01-audio.md](01-audio.md). Keep the
  speaker's mass in the **chest** (static), not the moving head — SG90s
  can't sling a speaker around, and the cable would fatigue.
- Route servo leads away from the WS2812B data line (PWM noise → glitchy
  pixels).
- PTT button mounts on the **stand/desk**, not on the head — visitors must
  not push the robot's face.
- All cables (2× USB audio + ESP32 USB) exit through **one strain-relieved
  opening** at the back, then run along the stand.

---

## Do Not

- ❌ Power servos from the ESP32 5V pin. Brownout reboots mid-demo.
- ❌ Skip the common ground. Servos will jitter and MQTT will look "flaky".
- ❌ Put servo signals on GPIO 0/2/12/15. Boot loop.
- ❌ Wire the mic or speaker anywhere near the ESP32. They're laptop devices.
- ❌ Mount the speaker in the moving head. Chest only.
- ❌ Hot-plug servos while powered. Spikes kill channels.
- ❌ Trust venue mains without the power bank staged as fallback.
