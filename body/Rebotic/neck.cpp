// Strict port of sholderNeck.ino's servo3 (neck, pin 4) -- literal
// NECK_MIN/MAX, literal per-state angles, literal nod gesture, same
// cosine easing, made non-blocking.
#include "neck.h"
#include "config.h"
#include <ESP32Servo.h>
#include <math.h>
#include <string.h>

enum NeckState { NECK_IDLE, NECK_SPEAK };   // only 2 states, per explicit request

static Servo panServo;
static bool attached = false;
static float currentAngle = 0.0f;
static NeckState currentState = NECK_IDLE;

// ─── literal sholderNeck.ino constants ─────────────────────────────────────
static const float NECK_MIN = 0.0f;
static const float NECK_MAX = 20.0f;
static const float IDLE_ANGLE  = 0.0f;
static const float SPEAK_ANGLE = 10.0f;
static const unsigned long MOVE_MS = 250;   // pose transition -- fast, so return-to-rest reads as immediate
static const unsigned long LEG_MS  = 500;   // one leg of the nod gesture

static float easeInOutCosine(float t) {
  return 0.5f - 0.5f * cosf(PI * t);   // sholderNeck.ino's exact easing formula
}

static void writeAngle(float angle) {
  currentAngle = constrain(angle, NECK_MIN, NECK_MAX);
  panServo.write((int)roundf(currentAngle));
}

// Non-blocking replacement for easeTo(): same cosine curve, sampled per
// tick from elapsed time instead of looped with delay().
struct Ease1 { float from, to; unsigned long start, durationMs; bool active; };
static Ease1 poseEase = {0, 0, 0, 0, false};

static void startPoseEase(float target, unsigned long durationMs) {
  poseEase = { currentAngle, target, millis(), durationMs, true };
}

static void tickPoseEase(unsigned long now) {
  if (!poseEase.active) return;
  unsigned long elapsed = now - poseEase.start;
  if (elapsed >= poseEase.durationMs) {
    writeAngle(poseEase.to);
    poseEase.active = false;
    return;
  }
  float eased = easeInOutCosine((float)elapsed / poseEase.durationMs);
  writeAngle(poseEase.from + (poseEase.to - poseEase.from) * eased);
}

static float targetFor(NeckState s) {
  return (s == NECK_SPEAK) ? SPEAK_ANGLE : IDLE_ANGLE;
}

// ─── gest "nod" -- literal gestureNod ───────────────────────────────────────
// Two legs (dip to NECK_MAX, then straight back, no hold), 500ms each,
// cosine-eased -- exact shape/timing from the reference file.
static bool nodActive = false;
static unsigned long nodStart = 0;
static float nodFromAngle = 0;

static bool tickNod(unsigned long now) {
  unsigned long elapsed = now - nodStart;
  const unsigned long total = LEG_MS * 2;
  if (elapsed >= total) return false;

  bool secondLeg = elapsed >= LEG_MS;
  unsigned long legElapsed = secondLeg ? elapsed - LEG_MS : elapsed;
  float eased = easeInOutCosine((float)legElapsed / LEG_MS);

  float from = secondLeg ? NECK_MAX : nodFromAngle;
  float to   = secondLeg ? nodFromAngle : NECK_MAX;
  writeAngle(from + (to - from) * eased);
  return true;
}

static void attachPan() {
  panServo.attach(PIN_NECK_PAN);
  attached = true;
}

void neckBegin() {
  attachPan();
  const unsigned long EASE_MS = 1000;
  unsigned long start = millis();
  unsigned long now;
  const float from = 90.0f;   // SG90 power-on default center
  do {
    now = millis();
    float t = constrain((now - start) / (float)EASE_MS, 0.0f, 1.0f);
    float eased = t < 0.5f ? 4 * t * t * t : 1 - powf(-2 * t + 2, 3) / 2;   // boot ease only
    writeAngle(from + eased * (IDLE_ANGLE - from));
    delay(15);
  } while (now - start < EASE_MS);

  currentState = NECK_IDLE;
}

void neckTick(unsigned long now) {
  // The reference file never detaches its servo -- attached once in
  // setup(), held forever. Matching that: no idle-timeout detach here
  // (listening sends no MQTT command at all, so detaching on command-
  // silence would let the neck sag under gravity during any listening
  // pause over 5s instead of holding its pose).
  if (!attached) attachPan();

  if (nodActive) {
    if (!tickNod(now)) nodActive = false;
    return;
  }
  tickPoseEase(now);
}

void neckOnCommand(const char* t, const char* v) {
  if (strcmp(t, "state") == 0) {
    // Only idle and speak exist, per explicit request.
    NeckState newState;
    if      (strcmp(v, "speak") == 0) newState = NECK_SPEAK;
    else if (strcmp(v, "idle") == 0)  newState = NECK_IDLE;
    else return;

    if (newState != currentState) {
      nodActive = false;   // a real state change cancels an in-flight nod
      currentState = newState;
      startPoseEase(targetFor(newState), MOVE_MS);
    }
  } else if (strcmp(t, "gest") == 0) {
    if (strcmp(v, "nod") == 0) {
      nodActive = true;
      nodStart = millis();
      nodFromAngle = currentAngle;
    }
    // "tilt" is arms.cpp's now; anything else ignored.
  }
  // t == "amp" or anything else: ignore too.
}
