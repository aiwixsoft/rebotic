# Rebotic — Hardware Parts List (rev 2)

Conversational expo robot · laptop = mind · body moves: neck, head, two arms
(2-axis each), LED eyes · wired USB audio · software push-to-talk (no
hardware button)

Prepared 2026-07-24 · matches body/specs/03-electronics.md (pending update)
· prices are approximate USD — local market prices vary

**Changed from rev 1:** removed the jaw servo + jaw pushrod linkage (no mouth
movement) and the arcade push-to-talk button (redundant — push-to-talk is
already handled in software, see `hearing.py`). Added a second servo per arm
so each arm has two independent axes ("moving all around" instead of just
up/down).

---

## 1 · Controller (the body's motion brain)

| Part (market name) | Qty | Description | Purpose in the robot | Approx. |
|---|---|---|---|---|
| **ESP32 DevKit V1 (30-pin)**<br>*search: "ESP32 DevKit V1 WROOM-32 development board"* | 1 | Small WiFi-enabled microcontroller board with USB port. Any ESP32-WROOM-32 dev board works. | Sits inside the body. Receives mood commands from the laptop over WiFi and drives all servos and eyes. | $5–8 |

---

## 2 · Movement (neck, head, arms)

| Part (market name) | Qty | Description | Purpose in the robot | Approx. |
|---|---|---|---|---|
| **SG90 9g micro servo**<br>*search: "Tower Pro SG90 9g micro servo motor"* | 6 + 2 spare | Tiny hobby servo, ~180° rotation, plastic gears. Comes with horns and mounting screws. | One each for: neck pan (turns left/right), head tilt (nods), left arm lift, right arm lift, **left arm rotate (new)**, **right arm rotate (new)** — the rotate servos are what let each arm swing forward/back instead of only up/down (wave, raise, open gestures). Spares are mandatory — plastic gears strip on long demo days. | $2–3 each |
| **Pan/tilt servo bracket kit (SG90 size)** — neck<br>*search: "SG90 pan tilt bracket kit 2 axis"* | 1 | Two-axis plastic mounting frame that holds two SG90s stacked. | The neck mechanism — bottom servo turns the head, top servo nods it. | $3–5 |
| **Small servo L-bracket (SG90 size)** — shoulders (new)<br>*search: "SG90 servo mounting L bracket"* | 2 | Small right-angle bracket that mounts one servo's horn onto a second servo's body, at 90°. | One per shoulder — mounts the new rotate servo perpendicular to the lift servo, so each arm gets two independent axes off a single shoulder point. | $1–2 each |

Removed from rev 1: *Pushrod / stiff steel wire* (jaw linkage) — no longer
needed, there's no jaw.

---

## 3 · Eyes & input

| Part (market name) | Qty | Description | Purpose in the robot | Approx. |
|---|---|---|---|---|
| **WS2812B 8-LED ring (5V)**<br>*search: "WS2812B 8 bit LED ring 5V NeoPixel"* | 2 | Ring of 8 individually-addressable RGB LEDs, single data wire, chainable. | The eyes — blink randomly, dim cyan at rest, bright white when attending, pulsing amber while thinking, eye-roll on shrug. | $2–4 each |

Removed from rev 1: *Arcade push button, 60mm* — push-to-talk stays
software-only (keyboard PTT already in `hearing.py`); no physical button.

---

## 4 · Ears & voice (USB audio — plugs into the LAPTOP, never the ESP32)

| Part (market name) | Qty | Description | Purpose in the robot | Approx. |
|---|---|---|---|---|
| **USB directional microphone**<br>*search: "USB cardioid gooseneck microphone" or "mini USB conference mic cardioid"* | 1 | Compact plug-and-play USB mic. Cardioid/directional pattern is the priority — it hears what's in front, rejects room noise. Worth spending extra here. | The robot's ears — mounted in the head/chest facing the visitor. The laptop listens through it via a USB cable. | $10–25 |
| **USB-powered mini speaker, 3–5W**<br>*search: "USB powered mini speaker 5V wired" (USB sound, or 3.5mm audio + USB power)* | 1 | Small full-range wired speaker powered over USB. 3–5W is plenty for one visitor at arm's length. | The robot's voice — mounted behind a grille in the chest. | $8–15 |
| **USB 2.0 extension cable, 3m (A-male to A-female)**<br>*search: "USB 2.0 extension cable 3m"* | 2 | Plain USB extension leads. | The hidden "tubes" — route mic and speaker from the body down the stand to the laptop. Plug into two separate laptop ports (not one hub). | $3–5 each |

---

## 5 · Power

| Part (market name) | Qty | Description | Purpose in the robot | Approx. |
|---|---|---|---|---|
| **5V 5A DC power adapter (mains)**<br>*search: "5V 5A power supply adapter 5.5mm barrel jack"* | 1 | Wall adapter, 5 volts at 5 amps, barrel plug. | Dedicated servo + LED power rail. **Six** stalled servos now pull ~4A (was ~3.3A with 5) — 5A is still the floor but the margin is tighter than rev 1. Never power servos from the ESP32 board. | $8–12 |
| **DC barrel jack to screw-terminal adapter (female)**<br>*search: "DC 5.5x2.1 female barrel jack screw terminal adapter"* | 1 | Lets you connect bare wires to the adapter's barrel plug, no soldering. | Taps the 5V supply into the servo power distribution wiring. | $1–2 |
| **Electrolytic capacitor, 1000µF / 10V**<br>*search: "1000uF 10V electrolytic capacitor"* | 1 (buy 2–3) | Large can-shaped capacitor. Mind the polarity stripe when wiring. | Sits across the servo power rail — absorbs the current spike when servos start moving so nothing resets. | <$1 |
| **Resistor, 470Ω, 1/4W**<br>*search: "470 ohm resistor 1/4W"* | 1 pack | Ordinary small resistor. | Goes in series with the eyes' LED data line — protects the first pixel from signal spikes. | <$1 |
| **Micro-USB data cable**<br>*search: "micro USB data cable" (must be DATA cable, not charge-only)* | 1 | Standard phone-style cable — verify it carries data. | Powers the ESP32's logic from the laptop and is how firmware gets flashed onto it. | $2–3 |

---

## 6 · Assembly & body shell

| Part (market name) | Qty | Description | Purpose in the robot | Approx. |
|---|---|---|---|---|
| **Dupont jumper wire set (M-M, M-F, F-F)**<br>*search: "dupont jumper wires 120pcs kit"* | 1 kit | Pre-crimped hookup wires for breadboard/header connections. | All wiring between ESP32, servos, and LEDs. Buy the big kit — spares are on the expo ship list. | $4–6 |
| **Breadboard, half-size (400 tie points)**<br>*search: "solderless breadboard 400 points"* | 1 | Solderless prototyping board. | Holds the ESP32 and the power distribution — no soldering needed for the whole build. | $3–5 |
| **Foam board / foam-core sheets, 5mm**<br>*search: "foam board A3 5mm" (art/stationery shop)* | 2–3 sheets | Lightweight rigid board, cuts with a knife. | The head shell and both arms. **Must stay light** — each arm now carries two servos instead of one, so keep the arm panels as thin/small as the added weight allows. | $3–6 |
| **Acoustic / packing foam scrap**<br>*search: "acoustic foam panel small" (or reuse packaging foam)* | small piece | Soft open-cell foam. | Isolation pad between speaker and mic mounts — stops the robot hearing its own voice and interrupting itself. | $0–3 |
| **Hot glue gun + sticks, zip ties, electrical tape**<br>*(household / any hardware shop)* | — | General assembly supplies. | Mounting servos in foam, strain-relieving the cables where they exit the body. | $5–8 |

---

**Estimated total: ~$58–95** · The USB microphone is still the one place to
spend more — a genuinely directional mic is the biggest single upgrade to
demo quality in a noisy hall.

## Buying tips

- **Already owned, not on the list:** the laptop (the mind), a phone (WiFi
  hotspot), and a wired headset mic (expo fallback input).
- **Servos:** buy all 8 (6 + 2 spare) from the same seller/batch so they
  behave identically. "MG90S" (metal gear) is a worthy ~$1/pc upgrade for
  the four arm servos if available — they now carry more duty cycle with
  two axes moving per arm.
- **Speaker:** if it has a 3.5mm audio plug + USB power plug instead of pure
  USB audio, that's fine — the laptop has both sockets.
- **Power adapter:** don't buy 5V 3A to save two dollars — 5A is the floor,
  and it's tighter now with 6 servos instead of 5. If budget allows, a 5V 6A
  adapter buys back the headroom rev 1 had.
- **Capacitor polarity:** electrolytic caps have a marked negative stripe —
  wiring one backwards destroys it.
- **Micro-USB cable:** test it transfers data (connect a phone) —
  charge-only cables are the classic "board won't flash" trap.

---

Rebotic body subproject · full wiring, pinout and power details:
`body/specs/03-electronics.md` (still describes the rev-1 jaw/button build —
needs updating to match this list) · assembly order: `body/specs/04-bringup.md`
