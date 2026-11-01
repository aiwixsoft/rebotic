#include "neck.h"
#include "config.h"
#include <ESP32Servo.h>
#include <math.h>
#include <string.h>

enum NeckState { NECK_IDLE, NECK_ATTEND, NECK_THINKING, NECK_SPEAK };

static Servo panServo;
static bool attached = false;
static float currentAngle = 90.0f;   // follower state, pose only (no sway)
static unsigned long lastTickMillis = 0;
static unsigned long lastMotionDemandMillis = 0;
static NeckState currentState = NECK_IDLE;

// Same speed-limited follower as arms.cpp uses (see that file's comment for
// the full reasoning: a fixed-duration eased curve reads mechanical and can
// jump if a tick is late; this nudges toward the target every tick, capped
// at a max speed, decelerating smoothly into rest). Kept as its own small
// copy rather than a shared header -- neck and arms are independent modules
// by design (spec 06/07), and this is only a few lines.
static const float FOLLOW_MAX_DEG_PER_SEC = 60.0f;
static const float FOLLOW_TIME_CONSTANT_S = 0.30f;

static float approach(float current, float target, float dt) {
  if (dt <= 0.0f) return current;
  float step = (target - current) * (1.0f - expf(-dt / FOLLOW_TIME_CONSTANT_S));
  float maxStep = FOLLOW_MAX_DEG_PER_SEC * dt;
  step = constrain(step, -maxStep, maxStep);
  return current + step;
}

// idle and speak both get the same gentle ambient sway (spec 06: speak
// "hold the attend pose + idle-style breathe sway underneath" -- the neck
// doesn't need dedicated speech choreography). attend/thinking hold dead
// still, same "reactive, settled" pattern arms.cpp uses.
struct Sway { float ampDeg, hz; };
static Sway swayFor(NeckState s) {
  if (s == NECK_IDLE || s == NECK_SPEAK) return { 10.0f, 0.2f };
  return { 0.0f, 0.0f };
}

static float swayAngle(unsigned long now) {
  Sway s = swayFor(currentState);
  if (s.ampDeg <= 0.0f) return 0.0f;
  float headroom = min((float)CAL_NECK_PAN.base - (float)CAL_NECK_PAN.minAngle,
                        (float)CAL_NECK_PAN.maxAngle - (float)CAL_NECK_PAN.base);
  float amp = min(s.ampDeg, max(0.0f, headroom));
  float phase = (now / 1000.0f) * TWO_PI * s.hz;
  return sinf(phase) * amp;
}

static void writeAngle(float angle) {
  float out = CAL_NECK_PAN.mirror ? (180.0f - angle) : angle;
  out = constrain(out, (float)CAL_NECK_PAN.minAngle, (float)CAL_NECK_PAN.maxAngle);
  panServo.write((int)roundf(out));
}

static void attachPan() {
  panServo.attach(PIN_NECK_PAN);
  attached = true;
}
static void detachPan() {
  panServo.detach();
  // Same floating-pin-noise fix as arms.cpp's detachAll() -- a floating
  // signal wire picks up ambient/coupled noise an SG90 reads as real pulse
  // commands, causing it to twitch with no command ever sent.
  pinMode(PIN_NECK_PAN, OUTPUT);
  digitalWrite(PIN_NECK_PAN, LOW);
  attached = false;
}

void neckBegin() {
  attachPan();
  const unsigned long EASE_MS = 1000;
  unsigned long start = millis();
  unsigned long now;
  const float from = 90.0f;  // SG90 power-on default center; only known reference at boot
  do {
    now = millis();
    float t = constrain((now - start) / (float)EASE_MS, 0.0f, 1.0f);
    float eased = t < 0.5f ? 4 * t * t * t : 1 - powf(-2 * t + 2, 3) / 2;
    float angle = from + eased * ((float)CAL_NECK_PAN.base - from);
    writeAngle(angle);
    currentAngle = angle;
    delay(15);
  } while (now - start < EASE_MS);

  currentState = NECK_IDLE;
  lastMotionDemandMillis = now;
  lastTickMillis = now;
}

void neckTick(unsigned long now) {
  unsigned long tickDeltaMs = (lastTickMillis == 0 || now < lastTickMillis) ? 0 : now - lastTickMillis;
  if (tickDeltaMs > 50) tickDeltaMs = 50;
  float dt = tickDeltaMs / 1000.0f;
  lastTickMillis = now;

  bool haveRecentDemand = (now - lastMotionDemandMillis) < SERVO_DETACH_IDLE_MS;
  if (!haveRecentDemand) {
    if (attached) detachPan();
    return;
  }
  if (!attached) attachPan();

  // No per-state pose offset (same "all states sit flat at base" pattern
  // arms.cpp uses) -- pan always settles to dead-center; only the sway on
  // top differs by state.
  currentAngle = approach(currentAngle, (float)CAL_NECK_PAN.base, dt);
  writeAngle(currentAngle + swayAngle(now));
}

void neckOnCommand(const char* t, const char* v) {
  lastMotionDemandMillis = millis();

  if (strcmp(t, "state") == 0) {
    if      (strcmp(v, "idle") == 0)     currentState = NECK_IDLE;
    else if (strcmp(v, "attend") == 0)   currentState = NECK_ATTEND;
    else if (strcmp(v, "thinking") == 0) currentState = NECK_THINKING;
    else if (strcmp(v, "speak") == 0)    currentState = NECK_SPEAK;
    // unknown state value -> ignore silently
  }
  // t == "gest" with "nod"/"tilt" -- both are tilt-axis motions; no tilt
  // servo exists on this board, so silently no-op (matches arms.cpp's
  // "unrecognized gest -> ignore silently" pattern). t == "amp" or
  // anything else: ignore too.
}
