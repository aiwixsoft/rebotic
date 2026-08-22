# Spec 07 — ARMS / HAND-GESTURE MOTION (shoulder lift + rotate + elbow)

**Scope, exactly:** the two arms — one servo per shoulder for lift, one per
shoulder for rotate, and **one servo per elbow** (mid-arm bend) — 6 servos
total, reacting to the existing MQTT `state`/`gest` contract, with the
primary goal of readable **hand gestures while the robot speaks**. Updated
from the shoulder-only (4-servo) design to add an elbow per arm, now that
12 servos are on hand instead of 8 — see Parts below.

**Sibling spec:** [06-neck-motion.md](06-neck-motion.md) covers the neck
(pan/tilt). Both halves end up sharing **one ESP32, one WiFi/MQTT
connection, and one servo-safety/fail-safe loop** — build these as separate
sketches for bench-testing if you like; merging later is just combining
`setup()`/`loop()` bodies and pin tables, not a redesign.

**Explicitly NOT in this spec:** jaw, eyes, mic/speaker, hardware PTT
button, and the neck — see below for the full out-of-scope reasoning (same
for both halves; kept here since this is the primary file for the
parts-reconciliation detail).

---

## Out of scope (deliberately, for this phase)

| Subsystem | Status | Why excluded here |
|---|---|---|
| Mouth | **OLED**, not a jaw servo | Face rendering is a software-on-a-display problem, not a motion problem. Needs its own spec once the OLED and its driver/library are chosen — not written yet. |
| Eyes | **OLED**, not WS2812 rings | Same reasoning — animated eyes drawn on the OLED, not RGB LEDs. |
| Neck | Separate spec | See [06-neck-motion.md](06-neck-motion.md) |
| Mic / speaker | Already done, external | USB mic + powered speaker wired straight to the laptop per [01-audio.md](01-audio.md). Nothing here touches audio. |
| Hardware PTT button | Not being built now | [02-firmware.md](02-firmware.md)'s `rebotic/evt`/button section still applies later if a physical button gets added; none in the current parts. |
| Wrist | Not built | Elbow is the added joint this revision — wrist/hand articulation is a further future addition, not assumed here. |

---

## Parts (arms-only slice)

Inventory updated: **12 SG90 servos on hand** (earlier spec assumed 8, from
the original receipts — confirm where the extra 4 came from if it matters
for warranty/batch-matching, otherwise just treat as one pool of 12).

| Have | Qty used here | Notes |
|---|---|---|
| ESP32 WROOM-32D DevKitC V4 | 1 (shared with neck) | one controller drives both halves eventually |
| SG90 9g micro servo, 180° | 6 (of the 12 on hand) | L-shoulder lift, L-shoulder rotate, L-elbow, R-shoulder lift, R-shoulder rotate, R-elbow. Neck (spec 06) uses 2 more. **4 spare** remain across the whole build. |
| L-shaped servo bracket (MG995/996 size) | 2 | one per shoulder | **check this** — sized for a bigger servo body than SG90. May need packing/shimming (foam strips, hot glue) to seat an SG90 snugly. Test-fit before final assembly, see Bring-up below. |
| 1000µF electrolytic cap | 1 (of 2) | servo rail smoothing — shared rail once merged with neck |
| DC barrel jack → screw terminal | 1 (of 2) | servo rail power tap |
| Dupont wires (M-F, M-M, F-F) | shared pool | signal/ground wiring |
| Breadboard 830pt | 1 | power distribution + prototyping |
| Hot glue gun + sticks, zip ties | — | assembly, incl. bracket shimming and elbow mounting |
| USB Type-C cable | 1 | flash/power ESP32 |

**Elbow mounting — not covered by either receipt.** There's no dedicated
elbow bracket in the parts list. The straightforward build: embed the elbow
servo's *body* in the upper-arm foam panel (hot-glued into a cut recess),
with the forearm panel attached to the servo horn — same "servo shaft IS
the joint" approach spec 03 used for the shoulder, just repeated mid-arm.
Keep the forearm panel small/light: it's now cantilevered off a second
small servo, not the main shoulder joint, so it has less torque budget to
work with, not more.

**Unused this phase (not tied to either arms or neck):**
- 470Ω resistor ×10 — was for WS2812 data line; not wired in (see Out of scope)
- WS2812 ring ×2 — not used this phase, see Out of scope
- KY-037 sound-detection module ×2 — unclear intended use (clap-to-wake?
  ambient noise gating?). If you had a purpose in mind, say so and it can
  get its own spec; otherwise treat as spare.

**Missing / needs confirming:**
- **External 5V servo supply.** With the elbow added, the combined rail
  (arms + neck) now needs more headroom than before — see Power budget.
  Confirm you have a supply that clears **6A**, not just the 5A floor the
  earlier 6-servo design assumed.
- **Foam/board for arm+head shells** — the "KT foam board 90×60cm ×5" on
  receipt 1 is listed under an aircraft-glider SKU; confirm those sheets
  are actually earmarked for the robot shell.

---

## Pin assignment

| GPIO | Function |
|---|---|
| 26 | left shoulder — lift servo signal |
| 33 | left shoulder — rotate servo signal |
| 4 | left elbow servo signal |
| 32 | right shoulder — lift servo signal |
| 13 | right shoulder — rotate servo signal |
| 16 | right elbow servo signal |

Reserved and **do not reuse** when merging with the neck board: GPIO 14, 27
belong to spec 06's pan/tilt servos. Avoid GPIO 0, 12, 15 (strapping pins)
and 34–39 (input-only) for any future arm-side addition.

---

## Power budget (arms alone, 6 servos)

| Load | Worst case | Rail |
|---|---|---|
| 6× SG90 stall | ~4A | external 5V supply |
| ESP32 (WiFi tx bursts) | ~0.5A | laptop USB or dev supply |
| **Standalone total** | **~4.5A** | fine on a 5A bench supply while testing arms solo |

**When merged with spec 06's 2 neck servos**, combined worst case is
6A+2×0.67A ≈ **~5.3A servos + 0.5A ESP32 ≈ ~5.8A**. The earlier "5A is the
floor" guidance (written for the 6-total-servo design) **no longer holds**
with 8 servos on one rail — budget a **6A supply minimum**, and treat 5A as
insufficient once the elbows are wired in. As before, gestures use one limb
at a time so true worst-case (all 8 stalled simultaneously) is unlikely in
practice — but don't rely on that margin instead of buying the right supply.

---

## Firmware behavior (arms rows only)

| Message | Arm reaction |
|---|---|
| `{"t":"state","v":"idle"}` | arms "breathe" ±3° slowly (shoulder + elbow both drift gently) |
| `{"t":"state","v":"attend"}` | small arm lift (~10°), elbow relaxes slightly open — must feel *reactive* |
| `{"t":"state","v":"thinking"}` | arms settle down, elbow folds in toward idle rest |
| `{"t":"state","v":"speak"}` | **open design decision — see below** |
| `{"t":"amp","v":0-255}` | received, ignored (no jaw on this board) |
| `{"t":"gest","v":"shrug"}` | both shoulders lift ~45°, elbows bend in slightly for a natural "shrug" silhouette, hold 400ms, drop |

`nod`/`tilt` are neck-only — see spec 06. Unknown `t`/`v`: ignore silently.
Adding a distinct new gesture value (e.g. a proper "wave" that primarily
drives the elbow) needs a software-side change to the `gest` contract in
`.claude/PROJECT.md` and `brain.py`'s gesture selection — out of scope here,
same caution as spec 02's PTT note: doable later, explicit request only.
For now the elbow makes the existing `shrug` read more naturally; it
doesn't add a new gesture name on its own.

### Open decision: what do the arms do *while speaking*?

This is the core of "hand gesture when speaking" — with no jaw and no
`amp`-driven motion left to fill that time, `state:"speak"` needs its own
behavior so the arms don't just freeze mid-reply. Two reasonable options:

1. **Ambient talk-sway** — small continuous arm motion (like idle
   breathing, but slightly more active, now with the elbow adding a second
   layer of drift instead of just the shoulder) for the whole `speak`
   duration. Simple, always-on, no per-word logic.
2. **Speech-boundary gestures only** — arms stay in the `attend` pose during
   speech, and `gest:"shrug"` messages (timed from `brain.py`'s reply logic)
   are the only motion, landing on emphasis points in the reply.

Pick one before Stage 3 firmware — it changes the `state:"speak"` row above.
Defaulting to **option 1** (ambient talk-sway) if you don't have a
preference, since it needs no software-side changes and matches "alive
while talking." The elbow makes option 1 noticeably more convincing than it
was shoulder-only, since two joints drifting independently reads as more
alive than one.

---

## Gesture engine

- One-shot overlays: borrow the shoulder + elbow servos for ~700ms, then
  return to the current state animation.
- Non-blocking — small keyframe player ticked from `loop()`. Never
  `delay()` inside a gesture.
- A gesture arriving mid-gesture **replaces** it, never queues.

---

## Motion profile — human-like feel

Same principle as spec 06: every joint move is a curve sampled over time,
never a single `servo.write()` jump. `t` = elapsed/duration, clamped
`0..1`, eased fraction blends `angle = start + eased * (target - start)`.
With the elbow added, each arm is now a **3-joint chain** instead of 2, so
the stagger step below matters more than it did.

### Ease-in-out — default for idle sway, transitions, gesture returns

```cpp
float easeInOutCubic(float t) {
  return t < 0.5f ? 4*t*t*t : 1 - pow(-2*t + 2, 3) / 2;
}
```

### Ease-out-back — gesture *arrivals* only (e.g. shrug reaching the raised pose)

```cpp
float easeOutBack(float t) {
  const float c1 = 1.70158f;
  const float c3 = c1 + 1;
  float u = t - 1;
  return 1 + c3*u*u*u + c1*u*u;
}
```

Use only for the strike half of `shrug`; use plain ease-in-out for the
settle back down.

### Shoulder → elbow stagger — overlapping action, now 3 joints deep

Don't start all three joints of an arm at the same instant. Chain the
delay outward from the body, the way a real arm's motion actually
propagates (shoulder leads, forearm follows):

```cpp
unsigned long gestureStart;
// shoulder lift reads:   now - gestureStart
// shoulder rotate reads: now - gestureStart - 80
// elbow reads:           now - gestureStart - 160     // trails both, ~80ms further out
```

This is the single biggest lever for making 3 servos read as "one arm"
instead of "three motors" — more so than with the shoulder-only design,
since a real elbow visibly lags a real shoulder's lead.

### Randomized amplitude & duration — no two gestures identical

```cpp
float jitterAmp   = 1.0f + random(-10, 11) / 100.0f;            // ±10%
unsigned long jitterDur = baseDurationMs * (100 + random(-15, 16)) / 100;  // ±15%
```

Roll once at trigger time, bake into that gesture — never re-roll mid-move.

### Anticipation — wind-up before the strike

`shrug`: shoulders dip ~5° down first (~100ms), *then* the real lift, with
the elbow trailing per the stagger above. Separates "intentional gesture"
from "servo snapped to a setpoint."

### Idle micro-motion — never truly still

```cpp
float breathe = sin(millis() / 1000.0f * TWO_PI * 0.2f) * 3.0f;  // ±3°, 0.2Hz
float angle = basePoseAngle + breathe;
```

Runs underneath every state, including `speak`. With the elbow present,
give it its own slightly slower/smaller breathe (e.g. ±2° at 0.15Hz,
different phase than the shoulder) rather than copying the shoulder's exact
sine — two joints breathing in lockstep looks synced-and-fake, slightly out
of phase looks alive.

### Per-tick assembly

```cpp
float t      = constrain((millis() - startTime) / (float)duration, 0.0f, 1.0f);
float eased  = currentEasingFn(t);
float angle  = startAngle + eased * (targetAngle - startAngle);
servo.write(angle + breathe);
```

Each of the 6 servos runs this independently, with its own stagger offset
folded into `startTime` and its own `breathe` phase.

---

## Servo safety

- `detach()` all 6 servos after 5s with no motion demand.
- Constrain every write to `[SERVO_MIN, SERVO_MAX]` per joint — the elbow
  especially needs a conservative range, since over-rotating it can drive
  the forearm panel into the upper-arm panel.
- On boot: attach → ease to neutral pose over 1s → enter `idle`.

## Fail-safe & watchdog

```
WiFi lost        → retry every 2s, exponential to 30s. Meanwhile: idle animation.
MQTT lost        → same, non-blocking reconnect in loop().
No cmd for 10s   → force state=idle.
Hardware WDT 8s  → esp_task_wdt, fed from loop().
```

This logic runs once per board — if/when merged with spec 06 onto one
ESP32, there's exactly one WiFi/MQTT/watchdog stack, not two.

---

## Bring-up (arms-only, standalone)

1. **Test-fit the L-brackets with an SG90 before gluing anything** — these
   brackets are sized for MG995/996; confirm the SG90 seats without play.
   Shim with thin foam strips + hot glue if loose.
2. **Mock up one elbow first** — embed a servo in a scrap foam block, attach
   a light forearm strip to the horn, confirm the joint holds the forearm's
   weight without drooping over a few minutes. Fix the design here before
   building both arms around it.
3. Servo sweep sketch, one at a time, all 6 wired to the external rail —
   watch for ESP32 resets during sweep.
4. All 6 sweeping simultaneously — the brownout test, now against the
   updated 6A-supply assumption.
5. Flash real firmware, drive `state`/`gest` via `mosquitto_pub`, confirm
   every row of the behavior table above, including the shoulder→elbow
   stagger being visible.
6. When ready to combine: merge into spec 06's board/sketch before full
   `main.py` integration (spec 04 Stage 4 gate — latency must not regress).

---

## Passes when

- `attend` arm-lift completes < 300ms after publish
- `shrug` plays without visibly straining or stalling any servo, even
  repeated back-to-back, and the elbow visibly trails the shoulder rather
  than moving in lockstep
- All 6 servos sweeping simultaneously does not reset the ESP32 (on the 6A
  supply)
- 30 min idle → all 6 servos detached (cool to the touch)
- Kill mosquitto mid-`speak` → arms idle within ~1s, recover alone when the
  broker returns
- Power-cycle at any moment → rejoins WiFi, resubscribes, resumes idle
- Forearm holds its idle pose without drooping over a 10-minute soak
  (elbow-specific — catches an underpowered/overloaded elbow joint)

---

## Failure modes

| Symptom | Cause |
|---|---|
| Servo buzzes/jitters, doesn't seat right in the L-bracket | MG995/996 bracket, SG90 body — shim it (see Bring-up step 1) |
| Forearm droops or sags over time | Elbow servo undersized for the forearm's weight/lever arm — lighten the forearm panel before upgrading the servo |
| Elbow and shoulder snap together instead of flowing | Stagger offset missing or too small — check the 80/160ms chain |
| Servos hot/buzzing at idle | Missing `detach()` |
| ESP32 resets during sweep/gesture | Servo rail undersized (recheck against the new ~5.8A combined figure) or missing common ground |
| Body ignores laptop after a WiFi blip | Blocking reconnect loop starving MQTT |
| Twitching arms | Something sent raw angles over MQTT — intent only, don't |
| Boot loop / won't flash | Servo signal landed on a strapping pin (0/12/15) |
