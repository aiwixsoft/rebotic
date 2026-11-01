#include "arms.h"
#include "config.h"
#include <ESP32Servo.h>
#include <math.h>
#include <string.h>

enum JointId {
  J_L_SHOULDER_LIFT, J_L_SHOULDER_ROTATE,
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
  float breathePhase;   // radians offset, so joints don't sway in lockstep
  bool staticHold;       // true: always hold cal.base, ignore sway
};

// Shoulder-rotate is frozen (staticHold = true) -- open jitter
// investigation, do not re-enable without hardware evidence it's fixed.
static Joint joints[JOINT_COUNT] = {
  { {}, PIN_L_SHOULDER_LIFT,   CAL_L_SHOULDER_LIFT,   ROLE_SHOULDER_LIFT,   0.0f, false },
  { {}, PIN_L_SHOULDER_ROTATE, CAL_L_SHOULDER_ROTATE, ROLE_SHOULDER_ROTATE, 1.7f, true  },
  { {}, PIN_R_SHOULDER_LIFT,   CAL_R_SHOULDER_LIFT,   ROLE_SHOULDER_LIFT,   0.6f, false },
  { {}, PIN_R_SHOULDER_ROTATE, CAL_R_SHOULDER_ROTATE, ROLE_SHOULDER_ROTATE, 2.3f, true  },
  { {}, PIN_R_ELBOW,           CAL_R_ELBOW,           ROLE_ELBOW,           4.4f, false },
};

static bool attached = false;
static unsigned long lastMotionDemandMillis = 0;
static bool rawZeroActive = false;   // {"t":"zero",...} bring-up override

// Speed-limited follower: nudges toward the target every tick instead of
// jumping to it, so a stalled loop() tick can't produce a visible snap.
static float currentBase[JOINT_COUNT];
static unsigned long lastTickMillis = 0;
static const float FOLLOW_MAX_DEG_PER_SEC = 70.0f;
static const float FOLLOW_TIME_CONSTANT_S = 0.30f;

static float approach(float current, float target, float dt) {
  if (dt <= 0.0f) return current;
  float step = (target - current) * (1.0f - expf(-dt / FOLLOW_TIME_CONSTANT_S));
  step = constrain(step, -FOLLOW_MAX_DEG_PER_SEC * dt, FOLLOW_MAX_DEG_PER_SEC * dt);
  return current + step;
}

static ArmState currentState = STATE_IDLE;

// Sway amplitude ramps in/out over this time constant instead of switching
// instantly, so starting/stopping speech fades the gesture rather than
// snapping it (also covers barge-in, which flips state away from speak).
static float swayEnvelope[JOINT_COUNT] = {0, 0, 0, 0, 0};
static const float ENVELOPE_TIME_CONSTANT_S = 0.6f;

// Per-joint amplitude/speed jitter, rerolled once per speak-turn so it
// doesn't look identical every time. A local xorshift32, not Arduino's
// random(): eyes.cpp's shared generator isn't reentrant across tasks and
// has corrupted frames before when called concurrently from two of them.
static uint32_t jitterRngState = 0x9E3779B9u;
static float nextJitter(float rangePct) {
  jitterRngState ^= jitterRngState << 13;
  jitterRngState ^= jitterRngState >> 17;
  jitterRngState ^= jitterRngState << 5;
  float unit = (float)(jitterRngState % 2001) / 1000.0f - 1.0f;
  return 1.0f + unit * rangePct;
}
static float speakAmpJitter[JOINT_COUNT] = {1, 1, 1, 1, 1};
static float speakHzJitter[JOINT_COUNT]  = {1, 1, 1, 1, 1};

static float easeInOutCubic(float t) {
  return t < 0.5f ? 4 * t * t * t : 1 - powf(-2 * t + 2, 3) / 2;
}

struct Sway { float shoulderAmpDeg, shoulderHz, elbowAmpDeg, elbowHz; };
static Sway swayFor(ArmState s) {
  // Matched to neck.cpp's 0.2Hz sway feel; amplitude as close to its 10deg
  // as each joint's own headroom safely allows (elbow has only ~10deg).
  if (s == STATE_SPEAK) return { 9.0f, 0.20f, 8.0f, 0.20f };
  return { 0.0f, 0.0f, 0.0f, 0.0f };
}

static float breatheFor(int i, unsigned long now) {
  Sway s = swayFor(currentState);
  float amp = (joints[i].role == ROLE_ELBOW) ? s.elbowAmpDeg : s.shoulderAmpDeg;
  float hz  = (joints[i].role == ROLE_ELBOW) ? s.elbowHz     : s.shoulderHz;
  amp *= speakAmpJitter[i];
  hz  *= speakHzJitter[i];

  float base = joints[i].cal.base;
  float phase = (now / 1000.0f) * TWO_PI * hz + joints[i].breathePhase;

  if (joints[i].role == ROLE_SHOULDER_LIFT) {
    // Lift's base sits near its own minAngle -- a symmetric sway would bang
    // the floor, so sway one-sided (0..amp) instead.
    float liftAmp = min(amp, max(0.0f, (float)joints[i].cal.maxAngle - base));
    return (sinf(phase) * 0.5f + 0.5f) * liftAmp;
  }

  float headroom = min(base - (float)joints[i].cal.minAngle, (float)joints[i].cal.maxAngle - base);
  amp = min(amp, max(0.0f, headroom));
  return sinf(phase) * amp;
}

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
  // Drive the pin LOW instead of leaving it floating after detach() --
  // a floating signal wire picks up noise an SG90 reads as real commands.
  for (int i = 0; i < JOINT_COUNT; i++) {
    joints[i].servo.detach();
    pinMode(joints[i].pin, OUTPUT);
    digitalWrite(joints[i].pin, LOW);
  }
  attached = false;
}

void armsBegin() {
  attachAll();
  const unsigned long EASE_MS = 1000;
  unsigned long start = millis();
  unsigned long now;
  do {
    now = millis();
    float eased = easeInOutCubic(constrain((now - start) / (float)EASE_MS, 0.0f, 1.0f));
    for (int i = 0; i < JOINT_COUNT; i++) {
      float from = 90.0f;  // SG90 power-on default center
      writeJoint(joints[i], from + eased * (joints[i].cal.base - from));
    }
    delay(15);
  } while (now - start < EASE_MS);

  currentState = STATE_IDLE;
  for (int i = 0; i < JOINT_COUNT; i++) currentBase[i] = joints[i].cal.base;
  jitterRngState ^= now;   // differ run to run, not security-sensitive
  lastMotionDemandMillis = now;
  lastTickMillis = now;
}

void armsTick(unsigned long now) {
  unsigned long tickDeltaMs = (lastTickMillis == 0 || now < lastTickMillis) ? 0 : now - lastTickMillis;
  if (tickDeltaMs > 50) tickDeltaMs = 50;   // clamp so a stalled tick can't catch up in one jump
  float dt = tickDeltaMs / 1000.0f;
  lastTickMillis = now;

  if (rawZeroActive) {
    if (!attached) attachAll();
    for (int i = 0; i < JOINT_COUNT; i++) {
      // Raw write, no clamp/mirror -- the point is the literal physical 0.
      // Elbow floored at 90: below that risks a forearm/upper-arm collision.
      float raw = (joints[i].role == ROLE_ELBOW) ? 90.0f : 0.0f;
      joints[i].servo.write((int)raw);
      currentBase[i] = raw;
    }
    return;
  }

  bool haveRecentDemand = (now - lastMotionDemandMillis) < SERVO_DETACH_IDLE_MS;
  if (!haveRecentDemand) {
    if (attached) detachAll();
    return;
  }
  if (!attached) attachAll();

  for (int i = 0; i < JOINT_COUNT; i++) {
    if (joints[i].staticHold) {
      currentBase[i] = joints[i].cal.base;
      writeJoint(joints[i], joints[i].cal.base);
      continue;
    }
    float envTarget = (currentState == STATE_SPEAK) ? 1.0f : 0.0f;
    swayEnvelope[i] += (envTarget - swayEnvelope[i]) * (1.0f - expf(-dt / ENVELOPE_TIME_CONSTANT_S));

    currentBase[i] = approach(currentBase[i], joints[i].cal.base, dt);
    writeJoint(joints[i], currentBase[i] + breatheFor(i, now) * swayEnvelope[i]);
  }
}

void armsOnCommand(const char* t, const char* v) {
  lastMotionDemandMillis = millis();

  if (strcmp(t, "zero") == 0) {
    rawZeroActive = (strcmp(v, "on") == 0);
    return;
  }

  if (strcmp(t, "state") == 0) {
    rawZeroActive = false;
    ArmState newState;
    if      (strcmp(v, "idle") == 0)     newState = STATE_IDLE;
    else if (strcmp(v, "attend") == 0)   newState = STATE_ATTEND;
    else if (strcmp(v, "thinking") == 0) newState = STATE_THINKING;
    else if (strcmp(v, "speak") == 0)    newState = STATE_SPEAK;
    else return;

    if (newState != currentState) {
      // Reroll jitter only on an actual entry into speak, not on a
      // duplicate "speak" message, so repeats don't restart the animation.
      if (newState == STATE_SPEAK) {
        for (int i = 0; i < JOINT_COUNT; i++) {
          if (joints[i].staticHold) continue;
          speakAmpJitter[i] = nextJitter(0.15f);
          speakHzJitter[i]  = nextJitter(0.20f);
        }
      }
      currentState = newState;
    }
  }
  // "gest"/"amp"/anything else -> ignore silently.
}
