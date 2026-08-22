#include "arms.h"
#include "config.h"
#include <ESP32Servo.h>
#include <math.h>
#include <string.h>

enum JointId {
  J_L_SHOULDER_LIFT, J_L_SHOULDER_ROTATE, J_L_ELBOW,
  J_R_SHOULDER_LIFT, J_R_SHOULDER_ROTATE, J_R_ELBOW,
  JOINT_COUNT
};

enum JointRole { ROLE_SHOULDER_LIFT, ROLE_SHOULDER_ROTATE, ROLE_ELBOW };
enum ArmState  { STATE_IDLE, STATE_ATTEND, STATE_THINKING, STATE_SPEAK };

struct Joint {
  Servo servo;
  uint8_t pin;
  ServoCal cal;
  JointRole role;
  unsigned long staggerMs;   // full stagger used by gestures (spec: 0/80/160)
  float breathePhase;        // radians, so joints don't breathe in lockstep
};

// Shoulder leads, rotate follows +80ms, elbow trails +160ms (spec 07:
// "shoulder -> elbow stagger, now 3 joints deep").
static Joint joints[JOINT_COUNT] = {
  { {}, PIN_L_SHOULDER_LIFT,   CAL_L_SHOULDER_LIFT,   ROLE_SHOULDER_LIFT,   0,   0.0f },
  { {}, PIN_L_SHOULDER_ROTATE, CAL_L_SHOULDER_ROTATE, ROLE_SHOULDER_ROTATE, 80,  1.7f },
  { {}, PIN_L_ELBOW,           CAL_L_ELBOW,           ROLE_ELBOW,           160, 3.1f },
  { {}, PIN_R_SHOULDER_LIFT,   CAL_R_SHOULDER_LIFT,   ROLE_SHOULDER_LIFT,   0,   0.6f },
  { {}, PIN_R_SHOULDER_ROTATE, CAL_R_SHOULDER_ROTATE, ROLE_SHOULDER_ROTATE, 80,  2.3f },
  { {}, PIN_R_ELBOW,           CAL_R_ELBOW,           ROLE_ELBOW,           160, 4.4f },
};

static bool attached = false;
static float lastAngle[JOINT_COUNT];
static unsigned long lastMotionDemandMillis = 0;

static ArmState currentState = STATE_IDLE;
static unsigned long transitionStart = 0;
static float transitionFromAngle[JOINT_COUNT];
// Quick state-to-state moves must land well under the 300ms "attend" budget
// (spec 07 Passes-when), so they use a lighter stagger than gestures do.
static const unsigned long TRANSITION_MS = 200;
static const float TRANSITION_STAGGER_SCALE = 0.25f;   // -> 0/20/40ms

static bool gestureActive = false;
static unsigned long gestureStart = 0;
static float gestureFromAngle[JOINT_COUNT];
static float gestureJitterAmp = 1.0f;
static float gestureJitterDurFactor = 1.0f;
static const unsigned long ANTICIPATION_MS = 100;
static const unsigned long STRIKE_MS       = 200;
static const unsigned long HOLD_MS         = 400;  // spec: "hold 400ms, drop"
static const unsigned long RETURN_MS       = 200;

// ─── Easing (spec 07 "Motion profile") ─────────────────────────────────────
static float easeInOutCubic(float t) {
  return t < 0.5f ? 4 * t * t * t : 1 - powf(-2 * t + 2, 3) / 2;
}
static float easeOutBack(float t) {
  const float c1 = 1.70158f;
  const float c3 = c1 + 1;
  float u = t - 1;
  return 1 + c3 * u * u * u + c1 * u * u;
}

// ─── Per-state pose / sway targets ──────────────────────────────────────────
struct Pose { float liftDelta, rotateDelta, elbowDelta; };
static Pose poseFor(ArmState s) {
  switch (s) {
    case STATE_ATTEND:   return { +10.0f, 0.0f, -10.0f };  // lift + elbow relaxes open
    default:              return {   0.0f, 0.0f,   0.0f };  // idle / thinking / speak rest pose
  }
}

struct Sway { float shoulderAmpDeg, shoulderHz, elbowAmpDeg, elbowHz; };
static Sway swayFor(ArmState s) {
  if (s == STATE_SPEAK) return { 6.0f, 0.40f, 5.0f, 0.35f };  // ambient talk-sway
  return { 3.0f, 0.20f, 2.0f, 0.15f };                        // idle breathe (also under attend/thinking)
}

static float statePoseAngle(int i) {
  Pose p = poseFor(currentState);
  float delta = 0.0f;
  switch (joints[i].role) {
    case ROLE_SHOULDER_LIFT:   delta = p.liftDelta;   break;
    case ROLE_SHOULDER_ROTATE: delta = p.rotateDelta; break;
    case ROLE_ELBOW:           delta = p.elbowDelta;  break;
  }
  return joints[i].cal.base + delta;
}

static float breatheFor(int i, unsigned long now) {
  Sway s = swayFor(currentState);
  float amp = (joints[i].role == ROLE_ELBOW) ? s.elbowAmpDeg : s.shoulderAmpDeg;
  float hz  = (joints[i].role == ROLE_ELBOW) ? s.elbowHz     : s.shoulderHz;
  return sinf((now / 1000.0f) * TWO_PI * hz + joints[i].breathePhase) * amp;
}

// ─── Servo write (mirror + constrain) ───────────────────────────────────────
static void writeJoint(Joint &j, float angle) {
  float out = j.cal.mirror ? (180.0f - angle) : angle;
  out = constrain(out, (float)j.cal.minAngle, (float)j.cal.maxAngle);
  j.servo.write((int)roundf(out));
}

static void attachAll() {
  for (int i = 0; i < JOINT_COUNT; i++) joints[i].servo.attach(joints[i].pin);
  attached = true;
}
static void detachAll() {
  for (int i = 0; i < JOINT_COUNT; i++) joints[i].servo.detach();
  attached = false;
}

// ─── Gesture: shrug (spec 07 — the only arm gesture; nod/tilt are neck-only) ─
static bool armsTickGesture(unsigned long now) {
  unsigned long a = (unsigned long)(ANTICIPATION_MS * gestureJitterDurFactor);
  unsigned long b = (unsigned long)(STRIKE_MS * gestureJitterDurFactor);
  unsigned long h = (unsigned long)(HOLD_MS * gestureJitterDurFactor);
  unsigned long r = (unsigned long)(RETURN_MS * gestureJitterDurFactor);
  unsigned long total = a + b + h + r;

  bool anyActive = false;
  for (int i = 0; i < JOINT_COUNT; i++) {
    unsigned long stagger = joints[i].staggerMs;
    unsigned long elapsed = (now > gestureStart + stagger) ? now - gestureStart - stagger : 0;
    if (elapsed < total) anyActive = true;

    float from = gestureFromAngle[i];
    float dipTarget = from;
    if (joints[i].role == ROLE_SHOULDER_LIFT) dipTarget = from - 5.0f * gestureJitterAmp;  // anticipation

    float strikeTarget = from;  // rotate: no explicit shrug target -> holds
    if (joints[i].role == ROLE_SHOULDER_LIFT) strikeTarget = joints[i].cal.base + 45.0f * gestureJitterAmp;
    else if (joints[i].role == ROLE_ELBOW)    strikeTarget = joints[i].cal.base + 15.0f * gestureJitterAmp;

    float angle;
    if (elapsed < a) {
      float t = a > 0 ? elapsed / (float)a : 1.0f;
      angle = from + easeInOutCubic(t) * (dipTarget - from);
    } else if (elapsed < a + b) {
      float t = b > 0 ? (elapsed - a) / (float)b : 1.0f;
      angle = dipTarget + easeOutBack(t) * (strikeTarget - dipTarget);
    } else if (elapsed < a + b + h) {
      angle = strikeTarget;
    } else if (elapsed < total) {
      float t = r > 0 ? (elapsed - a - b - h) / (float)r : 1.0f;
      angle = strikeTarget + easeInOutCubic(t) * (from - strikeTarget);
    } else {
      angle = from;  // this joint's done; hold until the slowest joint clears
    }

    float breathe = breatheFor(i, now);
    writeJoint(joints[i], angle + breathe);
    lastAngle[i] = angle + breathe;
  }
  return anyActive;
}

// ─── Public API ──────────────────────────────────────────────────────────
void armsBegin() {
  attachAll();
  const unsigned long EASE_MS = 1000;
  unsigned long start = millis();
  unsigned long now;
  do {
    now = millis();
    float t = constrain((now - start) / (float)EASE_MS, 0.0f, 1.0f);
    float eased = easeInOutCubic(t);
    for (int i = 0; i < JOINT_COUNT; i++) {
      float from = 90.0f;  // SG90 power-on default center; only known reference at boot
      float angle = from + eased * (joints[i].cal.base - from);
      writeJoint(joints[i], angle);
      lastAngle[i] = angle;
    }
    delay(15);
  } while (now - start < EASE_MS);

  currentState = STATE_IDLE;
  transitionStart = now;
  for (int i = 0; i < JOINT_COUNT; i++) transitionFromAngle[i] = lastAngle[i];
  lastMotionDemandMillis = now;
}

void armsTick(unsigned long now) {
  bool haveRecentDemand = (now - lastMotionDemandMillis) < SERVO_DETACH_IDLE_MS;

  if (!haveRecentDemand && !gestureActive) {
    if (attached) detachAll();
    return;
  }
  if (!attached) attachAll();

  if (gestureActive) {
    if (!armsTickGesture(now)) gestureActive = false;
    return;
  }

  for (int i = 0; i < JOINT_COUNT; i++) {
    float target = statePoseAngle(i);
    unsigned long stagger = (unsigned long)(joints[i].staggerMs * TRANSITION_STAGGER_SCALE);
    unsigned long elapsed = (now > transitionStart + stagger) ? now - transitionStart - stagger : 0;
    float t = constrain(elapsed / (float)TRANSITION_MS, 0.0f, 1.0f);
    float eased = easeInOutCubic(t);
    float base = transitionFromAngle[i] + eased * (target - transitionFromAngle[i]);
    float angle = base + breatheFor(i, now);
    writeJoint(joints[i], angle);
    lastAngle[i] = angle;
  }
}

void armsOnCommand(const char* t, const char* v) {
  unsigned long now = millis();
  lastMotionDemandMillis = now;

  if (strcmp(t, "state") == 0) {
    ArmState newState;
    if      (strcmp(v, "idle") == 0)     newState = STATE_IDLE;
    else if (strcmp(v, "attend") == 0)   newState = STATE_ATTEND;
    else if (strcmp(v, "thinking") == 0) newState = STATE_THINKING;
    else if (strcmp(v, "speak") == 0)    newState = STATE_SPEAK;
    else return;  // unknown state value -> ignore silently

    if (newState != currentState || !attached) {
      currentState = newState;
      transitionStart = now;
      for (int i = 0; i < JOINT_COUNT; i++) transitionFromAngle[i] = lastAngle[i];
    }
  } else if (strcmp(t, "gest") == 0) {
    if (strcmp(v, "shrug") == 0) {
      gestureActive = true;
      gestureStart = now;
      gestureJitterAmp = 1.0f + random(-10, 11) / 100.0f;         // +/-10%
      gestureJitterDurFactor = (100 + random(-15, 16)) / 100.0f;  // +/-15%
      for (int i = 0; i < JOINT_COUNT; i++) gestureFromAngle[i] = lastAngle[i];
    }
    // nod/tilt are neck-only (spec 06); any other gest value -> ignore silently
  }
  // t == "amp" (no jaw on this board) or anything unrecognized -> ignore silently
}
