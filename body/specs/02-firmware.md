# Spec 02 — FIRMWARE (ESP32 body)

**Module:** `firmware/rebotic_body/` (Arduino / PlatformIO — separate from the Python tree)
**Job:** Render laptop *intent* as physical motion and light. Send hardware *input* (push-to-talk button) back to the laptop.
**Owns:** ALL timing, easing, interpolation, and servo safety. The laptop never sends angles.

---

## Position in the system

```
                     MQTT over WiFi (broker = laptop)
Laptop (mind)  ────── rebotic/cmd ────→  ESP32 body   (output: servos + eyes)
               ←───── rebotic/evt ─────  ESP32 body   (input: PTT button)
               ←═══ USB mic / speaker ═══ body shell  (audio — spec 01, no ESP32 involvement)
```

The conversation pipeline runs entirely on the laptop. Audio never touches the
ESP32 — the mic and speaker are USB devices in the body shell ([01-audio.md](01-audio.md)).

The ESP32 is **optional at runtime** — `robot.py` already fails silent if the
broker is down. The reverse must also hold: the body must behave sanely when
the laptop goes quiet (see Fail-safe).

---

## Firmware stack

```
Arduino framework (PlatformIO env: esp32dev)
PubSubClient        # MQTT client
ESP32Servo          # LEDC-backed servo PWM
Adafruit_NeoPixel   # WS2812B eyes
ArduinoJson         # parse {"t":...,"v":...}
esp_task_wdt        # hardware watchdog (built-in)
```

---

## Subscribed: `rebotic/cmd` (QoS 0)

Same contract as the software PROJECT.md — the body implements the receiving side.

| Message | Meaning | Body reaction |
|---|---|---|
| `{"t":"state","v":"idle"}` | nobody engaged | slow pan sway ±10° @ 0.2Hz, arms "breathe" ±3° slowly, eye blink every 3–6s, jaw closed, eyes dim cyan |
| `{"t":"state","v":"attend"}` | speech detected | ease pan→center + tilt→up in 200ms, small arm lift (~10°), eyes full white — must feel *reactive* |
| `{"t":"state","v":"thinking"}` | LLM working | eyes pulse amber @ 1Hz, tilt oscillates ±5° slowly, arms settle down |
| `{"t":"state","v":"speak"}` | TTS playing | eyes steady white, jaw driven by `amp` |
| `{"t":"amp","v":0-255}` | mouth energy, 40Hz | jaw angle (mapping below) |
| `{"t":"gest","v":"nod"}` | gesture tag | tilt down-up, twice, ~700ms total |
| `{"t":"gest","v":"tilt"}` | gesture tag | tilt to one side ~15°, hold 400ms, return |
| `{"t":"gest","v":"shrug"}` | gesture tag | BOTH arms up ~45°, hold 400ms, drop + eyes roll animation |

Unknown `t` or `v`: **ignore silently**. The laptop may grow the protocol; old
firmware must never crash on new messages.

---

## Jaw ↔ amp mapping

The laptop already smooths RMS with EMA (software spec 03) and publishes at
40Hz. Since the speaker is *in the body* (spec 01), jaw and sound now come
from the same place — sync reads much stronger than with laptop speakers.
The body adds mechanical smoothing only:

```
target = JAW_CLOSED + (amp / 255.0) * (JAW_OPEN - JAW_CLOSED)
slew-limit toward target at ≤ 600°/s      # SG90 can do ~600°/s unloaded
```

```cpp
JAW_CLOSED = 10   // degrees — calibrate per build, leave mechanical margin
JAW_OPEN   = 40   // ~30° of travel is plenty for a jaw illusion
AMP_TIMEOUT_MS = 250   // no amp message for 250ms → snap jaw closed
```

`AMP_TIMEOUT_MS` is the mouth's dead-man switch: if the laptop dies mid-word
(or WiFi drops a burst), the jaw must not freeze open. The laptop always sends
`amp:0` on stop, but the body must not depend on it arriving.

---

## Gesture engine

- Gestures are **one-shot overlays**: they borrow the neck/arm servos for
  ~700ms, then return control to the current state animation. A gesture uses
  arms OR neck, never all five servos at once (power budget, spec 03).
- Non-blocking — implement as a small keyframe player ticked from `loop()`.
  Never `delay()` inside a gesture: it freezes the jaw during speech.
- A gesture arriving while one is playing **replaces** it (don't queue —
  queued gestures play late and look wrong).

---

## Published: `rebotic/evt` (QoS 0)

Hardware input path. The expo PTT is a physical button on the body/stand
(software spec 01 names PTT as the PRIMARY input at the expo).

```json
{"t":"ptt","v":"down"}    // button pressed  → laptop forces speech_start
{"t":"ptt","v":"up"}      // button released → laptop finalizes the turn
```

- Debounce 30ms in firmware. Publish **edges only**, never held-state spam.
- **Laptop-side glue needed:** `robot.py` currently only publishes. Add a
  subscriber for `rebotic/evt` that drives the same code path as the
  spacebar PTT in `hearing.py`. Contract lives here; the laptop change is
  ~20 lines — **unwritten until explicitly requested** (the one future change
  that touches the software tree).

---

## Servo safety (6-hour survival)

- **`detach()` all servos after 5s with no motion demand.** Holding current
  cooks SG90s over 6 hours. Re-`attach()` on the next command — reattach
  glitch is invisible from >1m away.
- Constrain every write to `[SERVO_MIN, SERVO_MAX]` per joint. A bad message
  must never slam a servo into its mechanical stop.
- On boot: attach → ease to neutral pose over 1s → enter `idle`. Never jump.

---

## Fail-safe & watchdog

```
WiFi lost        → retry every 2s, exponential to 30s. Meanwhile: idle animation.
MQTT lost        → same. PubSubClient reconnect in loop(), non-blocking.
No cmd for 10s   → force state=idle (laptop probably restarted).
Hardware WDT 8s  → esp_task_wdt, fed from loop(). Firmware WILL hang in 6h
                   without it.
```

The body must be **power-cycle safe at any moment**: unplug → replug → it
rejoins WiFi, resubscribes, resumes idle. No state on the laptop side to
reset, because the laptop only ever sends intent.

---

## Standalone test (no Python needed)

Flash, then drive it from any shell on the laptop:

```bash
mosquitto_pub -t rebotic/cmd -m '{"t":"state","v":"attend"}'
mosquitto_pub -t rebotic/cmd -m '{"t":"gest","v":"nod"}'
for v in 0 80 200 120 0; do mosquitto_pub -t rebotic/cmd -m "{\"t\":\"amp\",\"v\":$v}"; done
mosquitto_sub -t rebotic/evt -v    # press the button → see ptt down/up
```

`python robot.py` (existing smoke test) exercises the same messages end-to-end.

**Passes when:**
- `attend` head-turn completes < 300ms after publish (feels reactive)
- Jaw follows the amp sequence visibly, closes at the end
- Gestures play during jaw motion without stutter (non-blocking check)
- Button press appears on `rebotic/evt` < 100ms, exactly one edge per press
- Kill mosquitto mid-speech → jaw closes within 250ms, body idles, and it
  recovers alone when the broker returns
- Runs 30 min idle → servos are detached (cool to the touch)

---

## Failure modes

| Symptom | Cause |
|---|---|
| Jaw frozen open | Missing `AMP_TIMEOUT_MS` dead-man |
| Jaw chatters | Slew limit missing, or re-smoothing an already-EMA'd amp |
| Gesture freezes speech jaw | `delay()` in gesture code — use the keyframe player |
| Servos hot/buzzing at idle | Missing `detach()` |
| Body ignores laptop after WiFi blip | Blocking reconnect loop starving MQTT |
| Twitching head | Someone sent angles over MQTT. Don't. Intent only. |
| Boot loop / won't flash | Servo signal on a strapping pin — see [03-electronics.md](03-electronics.md) pinout |
