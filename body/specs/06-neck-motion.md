# Spec 06 — NECK MOTION (pan + tilt)

**Scope, exactly:** the two-axis neck — pan + tilt, 2 servos, mounted in the
Camera Mini Pan/Tilt Kit — reacting to the existing MQTT `state`/`gest`
contract. Split out from the combined arms+neck spec so the neck can be
built, wired, and bench-tested **independently** of the arms.

**Sibling spec:** [07-arms-motion.md](07-arms-motion.md) covers the two
arms (hand gestures). Both halves end up sharing **one ESP32, one WiFi/MQTT
connection, and one servo-safety/fail-safe loop** — if you implement them as
separate sketches for bench-testing, merging is just combining `setup()`/
`loop()` bodies and pin tables later, not a redesign. This spec is written
so the neck alone is fully buildable and testable first.

**Explicitly NOT in this spec:** jaw, eyes, mic/speaker, hardware PTT
button, and the arms — see [07-arms-motion.md](07-arms-motion.md)'s
"Out of scope" table for the full reasoning (OLED face, external audio,
etc.); it's identical for both halves and not repeated here.

---

## Parts (neck-only slice)

| Have | Qty used here | Notes |
|---|---|---|
| ESP32 WROOM-32D DevKitC V4 | 1 (shared with arms) | one controller drives both halves eventually |
| SG90 9g micro servo, 180° | 2 (of the 8 purchased) | neck pan, neck tilt — the other 4 + 2 spare belong to spec 07 |
| Camera Mini Pan/Tilt Kit XY axis (no servos) | 1 | you supply the 2 SG90s into this bracket |
| 1000µF electrolytic cap | 1 (of 2) | servo rail smoothing — shared rail once merged with arms |
| DC barrel jack → screw terminal | 1 (of 2) | servo rail power tap |
| Dupont wires, breadboard | shared pool | see spec 07, same kit |

**Unused-this-phase items** (WS2812 rings, KY-037 sound modules, 470Ω
resistors) aren't tied to the neck or the arms specifically — see the full
list and reasoning in [07-arms-motion.md](07-arms-motion.md)'s parts table,
not repeated here.

**External 5V servo supply:** if bench-testing the neck alone, 2 SG90s draw
~1.3A stall — a modest 5V/2A supply is plenty for standalone testing. Once
merged with the arms (spec 07), size the final shared rail for the combined
6-servo load (~4A, 5A floor) — don't buy two separate small supplies
expecting to keep them.

---

## Pin assignment

| GPIO | Function |
|---|---|
| 14 | neck pan servo signal |
| 27 | neck tilt servo signal |

Reserved and **do not reuse** when merging with the arms board: GPIO 26, 33,
32, 13, 4, 16 belong to spec 07's shoulder + elbow servos (6 arm servos as
of the elbow-added revision). Avoid GPIO 0, 12, 15 (strapping pins) and
34–39 (input-only) for any future neck-side addition.

---

## Power budget (neck alone)

| Load | Worst case | Rail |
|---|---|---|
| 2× SG90 stall | ~1.3A | external 5V supply |
| ESP32 (WiFi tx bursts) | ~0.5A | laptop USB or dev supply |
| **Standalone total** | **~1.8A** | fine on a bench supply while testing solo |

When merged with spec 07's 6 arm servos (shoulder ×2 + elbow, per arm),
combined worst case is ~5.3A servos + ESP32 ≈ ~5.8A — spec 07 flags this as
needing a **6A supply minimum**, not the 5A floor the earlier 6-total-servo
design assumed. Gestures still use arms OR neck, never all 8 stalled at
once, but size the supply for the number on paper, not the lucky case.

---

## Firmware behavior (neck rows only)

| Message | Neck reaction |
|---|---|
| `{"t":"state","v":"idle"}` | slow pan sway ±10° @ 0.2Hz |
| `{"t":"state","v":"attend"}` | ease pan→center + tilt→up in 200ms — must feel *reactive* |
| `{"t":"state","v":"thinking"}` | tilt oscillates ±5° slowly |
| `{"t":"state","v":"speak"}` | hold the `attend` pose + idle-style breathe sway underneath (see Motion profile) — the neck doesn't need dedicated speech choreography the way arms do; steady-but-alive is enough |
| `{"t":"amp","v":0-255}` | received, ignored (no jaw on this board) |
| `{"t":"gest","v":"nod"}` | tilt down-up, twice, ~700ms total |
| `{"t":"gest","v":"tilt"}` | tilt to one side ~15°, hold 400ms, return |

`shrug` is arms-only — see spec 07. Unknown `t`/`v`: ignore silently.

---

## Gesture engine

- One-shot overlays: borrow the pan/tilt servos for ~700ms, then return to
  the current state animation.
- Non-blocking — small keyframe player ticked from `loop()`. Never
  `delay()` inside a gesture.
- A gesture arriving mid-gesture **replaces** it, never queues.

---

## Motion profile — human-like feel

Same principle as spec 07: every joint move is a curve sampled over time,
never a single `servo.write()` jump. `t` = elapsed/duration, clamped
`0..1`, eased fraction blends `angle = start + eased * (target - start)`.

### Ease-in-out — default for idle sway, transitions, gesture returns

```cpp
float easeInOutCubic(float t) {
  return t < 0.5f ? 4*t*t*t : 1 - pow(-2*t + 2, 3) / 2;
}
```

### Ease-out-back — gesture *arrivals* only (e.g. the tilt gesture's turn)

```cpp
float easeOutBack(float t) {
  const float c1 = 1.70158f;
  const float c3 = c1 + 1;
  float u = t - 1;
  return 1 + c3*u*u*u + c1*u*u;
}
```

### Pan/tilt stagger — overlapping action

Whenever pan and tilt move together (chiefly `attend`'s "pan→center +
tilt→up"), offset tilt's clock by 50–100ms so the two axes don't land in
perfect lockstep:

```cpp
unsigned long moveStart;
// pan reads:  now - moveStart
// tilt reads: now - moveStart - 80
```

### Randomized amplitude & duration — no two gestures identical

```cpp
float jitterAmp   = 1.0f + random(-10, 11) / 100.0f;            // ±10%
unsigned long jitterDur = baseDurationMs * (100 + random(-15, 16)) / 100;  // ±15%
```

Roll once at trigger time, bake into that gesture — never re-roll mid-move.

### Anticipation — wind-up before the strike

`nod`: tilt up ~5° first (~100ms), *then* the real down-up motion. Separates
"intentional gesture" from "servo snapped to a setpoint."

### Idle micro-motion — never truly still

```cpp
float breathe = sin(millis() / 1000.0f * TWO_PI * 0.2f) * 3.0f;  // ±3°, 0.2Hz
float angle = basePoseAngle + breathe;
```

Runs underneath every state, including `speak` — this is what carries the
neck through speech without dedicated choreography.

### Per-tick assembly

```cpp
float t      = constrain((millis() - startTime) / (float)duration, 0.0f, 1.0f);
float eased  = currentEasingFn(t);
float angle  = startAngle + eased * (targetAngle - startAngle);
servo.write(angle + breathe);
```

---

## Servo safety

- `detach()` both servos after 5s with no motion demand.
- Constrain every write to `[SERVO_MIN, SERVO_MAX]` per joint.
- On boot: attach → ease to neutral pose over 1s → enter `idle`.

## Fail-safe & watchdog

```
WiFi lost        → retry every 2s, exponential to 30s. Meanwhile: idle animation.
MQTT lost        → same, non-blocking reconnect in loop().
No cmd for 10s   → force state=idle.
Hardware WDT 8s  → esp_task_wdt, fed from loop().
```

This logic runs once per board — if/when merged with spec 07 onto one
ESP32, there's exactly one WiFi/MQTT/watchdog stack, not two.

---

## Bring-up (neck-only, standalone)

1. Assemble the pan/tilt kit with the 2 SG90s, confirm both axes move
   freely through their full range before wiring anything.
2. Servo sweep sketch, pan then tilt individually, powered from the
   external rail — watch for ESP32 resets during sweep.
3. Both sweeping simultaneously — brownout check.
4. Flash real firmware, drive `state`/`gest` via `mosquitto_pub`, confirm
   every row of the behavior table above.
5. When ready to combine: merge into spec 07's board/sketch before full
   `main.py` integration (spec 04 Stage 4 gate — latency must not regress).

---

## Passes when

- `attend` neck-turn completes < 300ms after publish
- `nod` and `tilt` gestures play without visibly straining the servo, even
  back-to-back
- Both servos sweeping simultaneously does not reset the ESP32
- 30 min idle → both servos detached (cool to the touch)
- Kill mosquitto mid-`speak` → neck idles within ~1s, recovers alone when
  the broker returns
- Power-cycle at any moment → rejoins WiFi, resubscribes, resumes idle

---

## Failure modes

| Symptom | Cause |
|---|---|
| Pan/tilt kit binds or grinds | Kit not seated/screwed flush before servo install — recheck assembly |
| Servos hot/buzzing at idle | Missing `detach()` |
| ESP32 resets during sweep/gesture | Servo rail undersized or missing common ground |
| Body ignores laptop after a WiFi blip | Blocking reconnect loop starving MQTT |
| Twitching neck | Something sent raw angles over MQTT — intent only, don't |
| Boot loop / won't flash | Servo signal landed on a strapping pin (0/12/15) |
