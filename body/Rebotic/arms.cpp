// Strict port of sholderNeck.ino's shoulder pair (servo1=pin13 rotate,
// servo2=pin32 lift) -- same constants, same pose/gesture shapes, same
// cosine easing, made non-blocking. L_SHOULDER_LIFT/L_SHOULDER_ROTATE/
// R_ELBOW aren't part of that file; they just hold at rest.
#include "arms.h"
#include "config.h"
#include <ESP32Servo.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>

enum ArmState { STATE_IDLE, STATE_SPEAK };   // only 2 states, per explicit request

// ─── servo1/servo2 -- literal sholderNeck.ino constants ────────────────────
static Servo servo1;   // R_SHOULDER_ROTATE, pin 13
static Servo servo2;   // R_SHOULDER_LIFT,   pin 32

static const float SERVO12_MIN = 90.0f;    // gesture (tilt/shrug) range + overall clamp ceiling
static const float SERVO12_MAX = 180.0f;
static const float HARD_MIN = 0.0f;        // idle only goes below SERVO12_MIN, per explicit request

static const float REST_S1  = 180.0f;        // idle target, pin13/servo1 -- per explicit request
static const float REST_S2  = 0.0f;          // idle target, pin32/servo2
static const float SHRUG_S1 = SERVO12_MAX;   // 180
static const float SHRUG_S2 = SERVO12_MIN;   // 90
static const float TILT_S1  = SERVO12_MAX;   // 180
static const float TILT_S2  = SERVO12_MAX;   // 180

static const unsigned long MOVE_MS = 250;   // pose transition -- fast, so return-to-rest reads as immediate
static const unsigned long LEG_MS  = 500;   // one leg of a gesture

static const unsigned long SPEAK_STEP_MIN_MS = 250;
static const unsigned long SPEAK_STEP_MAX_MS = 550;

static float currentS1 = REST_S1;
static float currentS2 = REST_S2;

static float easeInOutCosine(float t) {
  return 0.5f - 0.5f * cosf(PI * t);   // sholderNeck.ino's exact easing formula
}

static void writeS1S2(float s1, float s2) {
  // HARD_MIN (0), not SERVO12_MIN (90), is the floor here -- idle targets
  // 0 on both servos now, below the gesture/speak range, so the clamp has
  // to let it actually get there instead of catching it back at 90.
  currentS1 = constrain(s1, HARD_MIN, SERVO12_MAX);
  currentS2 = constrain(s2, HARD_MIN, SERVO12_MAX);
  servo1.write((int)roundf(currentS1));
  servo2.write((int)roundf(currentS2));
}

// Non-blocking replacement for easeTo(): same cosine curve, sampled per
// tick from elapsed time instead of looped with delay().
struct Ease2 { float fromS1, toS1, fromS2, toS2; unsigned long start, durationMs; bool active; };
static Ease2 poseEase = {0, 0, 0, 0, 0, 0, false};

static void startPoseEase(float s1Target, float s2Target, unsigned long durationMs) {
  poseEase = { currentS1, s1Target, currentS2, s2Target, millis(), durationMs, true };
}

static void tickPoseEase(unsigned long now) {
  if (!poseEase.active) return;
  unsigned long elapsed = now - poseEase.start;
  if (elapsed >= poseEase.durationMs) {
    writeS1S2(poseEase.toS1, poseEase.toS2);
    poseEase.active = false;
    return;
  }
  float eased = easeInOutCosine((float)elapsed / poseEase.durationMs);
  writeS1S2(poseEase.fromS1 + (poseEase.toS1 - poseEase.fromS1) * eased,
            poseEase.fromS2 + (poseEase.toS2 - poseEase.fromS2) * eased);
}

// ─── gest "tilt"/"shrug" -- literal gestureTilt/gestureShrug ───────────────
// Two legs (out, then straight back, no hold), 500ms each, cosine-eased --
// exact shape/timing from the reference file.
enum ArmGesture { GESTURE_NONE, GESTURE_TILT, GESTURE_SHRUG };
static ArmGesture activeGesture = GESTURE_NONE;
static unsigned long gestureStart = 0;
static float gestureBaseS1 = 0, gestureBaseS2 = 0;

static bool tickGesture(unsigned long now) {
  unsigned long elapsed = now - gestureStart;
  const unsigned long total = LEG_MS * 2;
  if (elapsed >= total) return false;

  bool secondLeg = elapsed >= LEG_MS;
  unsigned long legElapsed = secondLeg ? elapsed - LEG_MS : elapsed;
  float eased = easeInOutCosine((float)legElapsed / LEG_MS);

  float peakS1 = (activeGesture == GESTURE_TILT) ? TILT_S1 : SHRUG_S1;
  float peakS2 = (activeGesture == GESTURE_TILT) ? TILT_S2 : SHRUG_S2;

  float fromS1 = secondLeg ? peakS1 : gestureBaseS1, toS1 = secondLeg ? gestureBaseS1 : peakS1;
  float fromS2 = secondLeg ? peakS2 : gestureBaseS2, toS2 = secondLeg ? gestureBaseS2 : peakS2;

  writeS1S2(fromS1 + (toS1 - fromS1) * eased, fromS2 + (toS2 - fromS2) * eased);
  return true;
}

// ─── speak -- literal poseSpeak, made continuous instead of fixed-duration ─
// The reference file caps this at SPEAK_ANIMATION_MS=2700 because its own
// demo loop holds every command for a fixed 3s; here "speak" naturally
// lasts as long as the real utterance does, so the random glide just
// free-runs for as long as state == speak, same step-timing/shape per leg.
// Local xorshift32, not Arduino's random(): eyes.cpp documents that
// random()'s shared generator isn't reentrant across FreeRTOS tasks and
// has corrupted frames before when called concurrently from two of them.
static uint32_t rngState = 0x9E3779B9u;
static uint32_t nextRand() {
  rngState ^= rngState << 13;
  rngState ^= rngState >> 17;
  rngState ^= rngState << 5;
  return rngState;
}
static long randRange(long lo, long hiExclusive) {
  return lo + (long)(nextRand() % (uint32_t)(hiExclusive - lo));
}

// Speak range, per explicit request: pin13/servo1 glides within
// [135,180], pin32/servo2 within [0,45] -- each independently randomized
// (no more mirrored/opposite coupling between them).
static const float SPEAK_S1_MIN = 135.0f, SPEAK_S1_MAX = 180.0f;
static const float SPEAK_S2_MIN = 0.0f,   SPEAK_S2_MAX = 45.0f;

// Real speech-activity gate: voice.py publishes the real RMS amplitude of
// the audio actually playing (0-255, 40Hz, forced to 0 the instant the
// playback buffer runs dry -- same signal mouth.cpp uses for lip-sync).
// state=="speak" covers the whole utterance including inter-clause
// pauses, but amp only goes nonzero while sound is actually coming out --
// gating movement on amp (not just state) keeps the wave from wandering
// through silent gaps or lingering after the last real sound stops.
static const int AMP_SILENCE_THRESHOLD = 4;
static bool speakingActive = false;

static unsigned long speakStepStart = 0;
static unsigned long speakStepMs = 1;
static float speakFromS1, speakToS1, speakFromS2, speakToS2;

static void pickSpeakStep() {
  speakFromS1 = currentS1;
  speakFromS2 = currentS2;
  speakToS1 = (float)randRange((long)SPEAK_S1_MIN, (long)SPEAK_S1_MAX + 1);
  speakToS2 = (float)randRange((long)SPEAK_S2_MIN, (long)SPEAK_S2_MAX + 1);
  speakStepMs = randRange(SPEAK_STEP_MIN_MS, SPEAK_STEP_MAX_MS + 1);
  speakStepStart = millis();
}

static void tickSpeak(unsigned long now) {
  unsigned long elapsed = now - speakStepStart;
  if (elapsed >= speakStepMs) { pickSpeakStep(); elapsed = 0; }
  float eased = easeInOutCosine((float)elapsed / (float)speakStepMs);
  writeS1S2(speakFromS1 + (speakToS1 - speakFromS1) * eased,
            speakFromS2 + (speakToS2 - speakFromS2) * eased);
}

// ─── the 3 joints outside sholderNeck.ino's model -- held at rest ─────────
struct IdleJoint { Servo servo; uint8_t pin; ServoCal cal; };
static IdleJoint idleJoints[3] = {
  { {}, PIN_L_SHOULDER_LIFT,   CAL_L_SHOULDER_LIFT   },
  { {}, PIN_L_SHOULDER_ROTATE, CAL_L_SHOULDER_ROTATE },
  { {}, PIN_R_ELBOW,           CAL_R_ELBOW           },
};

static void writeIdleJoint(IdleJoint &j, float angle) {
  float out = j.cal.mirror ? (180.0f - angle) : angle;
  out = constrain(out, (float)j.cal.minAngle, (float)j.cal.maxAngle);
  j.servo.write((int)roundf(out));
}

// ─── attach -- all 5 physical servos this module owns, attached forever ──
static bool attached = false;
static bool rawZeroActive = false;

static void attachAll() {
  servo1.attach(PIN_R_SHOULDER_ROTATE);
  servo2.attach(PIN_R_SHOULDER_LIFT);
  for (int i = 0; i < 3; i++) idleJoints[i].servo.attach(idleJoints[i].pin);
  attached = true;
}

static ArmState currentState = STATE_IDLE;

void armsBegin() {
  attachAll();
  const unsigned long EASE_MS = 1000;
  unsigned long start = millis();
  unsigned long now;
  do {
    now = millis();
    float t = constrain((now - start) / (float)EASE_MS, 0.0f, 1.0f);
    float eased = t < 0.5f ? 4 * t * t * t : 1 - powf(-2 * t + 2, 3) / 2;   // boot ease only
    float from = 90.0f;   // SG90 power-on default center
    writeS1S2(from + eased * (REST_S1 - from), from + eased * (REST_S2 - from));
    for (int i = 0; i < 3; i++)
      writeIdleJoint(idleJoints[i], from + eased * (idleJoints[i].cal.base - from));
    delay(15);
  } while (now - start < EASE_MS);

  currentState = STATE_IDLE;
  rngState ^= now;   // differ run to run, not security-sensitive
}

void armsTick(unsigned long now) {
  if (rawZeroActive) {
    if (!attached) attachAll();
    writeS1S2(0.0f, 0.0f);   // raw, no clamp -- bring-up override, literal physical 0
    for (int i = 0; i < 3; i++) {
      // Elbow floored at 90: below that risks a forearm/upper-arm collision.
      bool isElbow = (idleJoints[i].pin == PIN_R_ELBOW);
      idleJoints[i].servo.write(isElbow ? 90 : 0);
    }
    return;
  }

  // The reference file never detaches its servos -- attached once in
  // setup(), held forever. Matching that exactly: no idle-timeout detach
  // here. (Listening sends no MQTT command at all now that attend/thinking
  // are gone, so detaching on command-silence would let these servos go
  // slack/sag under gravity during any listening pause over 5s -- that's
  // not holding the rest pose, it's just unpowered.)
  if (!attached) attachAll();

  for (int i = 0; i < 3; i++) writeIdleJoint(idleJoints[i], idleJoints[i].cal.base);

  if (activeGesture != GESTURE_NONE) {
    if (!tickGesture(now)) activeGesture = GESTURE_NONE;
    return;
  }
  if (currentState == STATE_SPEAK) {
    // Only actually glide while real audio is playing -- freeze in place
    // during an inter-clause pause (amp dropped to 0) instead of wandering
    // on through it with nothing being said.
    if (speakingActive) tickSpeak(now);
    return;
  }
  tickPoseEase(now);
}

void armsOnCommand(const char* t, const char* v) {
  if (strcmp(t, "zero") == 0) {
    rawZeroActive = (strcmp(v, "on") == 0);
    return;
  }

  if (strcmp(t, "state") == 0) {
    rawZeroActive = false;
    // Only idle and speak exist, per explicit request -- any other value
    // (there shouldn't be any now that the Python side only sends these
    // two) collapses to idle rather than being rejected.
    ArmState newState;
    if      (strcmp(v, "speak") == 0) newState = STATE_SPEAK;
    else if (strcmp(v, "idle") == 0)  newState = STATE_IDLE;
    else return;

    if (newState != currentState) {
      activeGesture = GESTURE_NONE;   // a real state change cancels an in-flight gesture
      currentState = newState;
      // poseIdle/poseAttend/poseThinking all target REST_S1,REST_S2 in the
      // reference file -- shoulders only ever move for speak or a gesture.
      if (newState == STATE_SPEAK) {
        pickSpeakStep();
        speakingActive = true;   // move immediately -- don't wait on the first amp message
      } else {
        startPoseEase(REST_S1, REST_S2, MOVE_MS);
      }
    }
  } else if (strcmp(t, "gest") == 0) {
    rawZeroActive = false;
    if (strcmp(v, "tilt") == 0) activeGesture = GESTURE_TILT;
    else if (strcmp(v, "shrug") == 0) activeGesture = GESTURE_SHRUG;
    else return;   // "nod" is neck.cpp's -- anything else ignored
    gestureStart = millis();
    gestureBaseS1 = currentS1;
    gestureBaseS2 = currentS2;
  } else if (strcmp(t, "amp") == 0) {
    // Real-time speech-activity gate for the speak wave -- see declaration.
    speakingActive = atoi(v) > AMP_SILENCE_THRESHOLD;
  }
  // anything else -> ignore silently.
}
