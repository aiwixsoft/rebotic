// Rebotic — eyes (2x OLED). See eyes.h.
//
// Wiring: 4-pin I2C SSD1306 board (GND, VDD, SCK, SDA), same fixed I2C
// address as the mouth panel -- so each eye gets its OWN dedicated pair of
// SDA/SCL pins, no shared wiring, no address conflict:
//
//   right eye: SDA=GPIO21, SCL=GPIO22
//   left eye:  SDA=GPIO18, SCL=GPIO19
//
// See mouth.cpp for why these are U8g2 software-I2C instances rather than
// hardware I2C or Adafruit_SSD1306 -- same reasoning applies to both.
//
// Rendering: sclera is fully static -- fixed rounded-box, drawn once and
// never touched again. The pupil ("retina") glides smoothly through
// left -> front -> right -> front -> left..., forever. Both eyes always
// mirror the same offset.
//
// IMPORTANT -- eyesTick() (the code that computes the pupil's offset and
// draws it into each display's RAM buffer) is driven by the single shared
// animation task in face.cpp, NOT its own task -- same reasoning as
// mouth.cpp: an earlier version gave eyes and mouth each their own
// independent FreeRTOS task, both bit-banging separate software-I2C buses
// concurrently and, worse, both calling Arduino's random() concurrently
// (random()'s internal generator state isn't reentrant), which was
// corrupting frames on one or both displays ("mixing everything up").
// eyesTick() only ever runs from that one shared task, so there's still
// only one caller of random() and one thing drawing into RAM buffers.
//
// What DOES run in parallel is the final flush -- actually pushing each
// eye's already-drawn buffer over I2C. That transfer is the slow part
// (bit-banged), and running it for eyeR then eyeL back-to-back on one core
// means whichever goes first visibly moves before the other by however
// long its own transfer takes. Fixed by giving each eye its own dedicated
// FreeRTOS task pinned to a different ESP32 core (eyeFlushTaskR on core 0,
// eyeFlushTaskL on core 1) that does ONLY the flush -- no drawing, no
// random(), no shared mutable state beyond the semaphores below -- so the
// two transfers genuinely happen at the same time in hardware instead of
// one after another. updateEyes() signals both via a "go" semaphore once
// both buffers are drawn, then waits on both "done" semaphores before
// returning -- this also guarantees the next frame's clearBuffer()/drawXXX()
// (which mutate the very buffer being transmitted) can't start until both
// transfers have actually finished, so frames never tear.
#include "eyes.h"
#include <U8g2lib.h>
#include <math.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

#define EYE_R_SDA 21
#define EYE_R_SCL 22
#define EYE_L_SDA 18
#define EYE_L_SCL 19

#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
#define SCREEN_CENTER_X (SCREEN_WIDTH / 2)
#define SCREEN_CENTER_Y (SCREEN_HEIGHT / 2)

#define EYE_W          84
#define EYE_H_OPEN     50
#define PUPIL_R         14

// Tile region (8px/tile) covering the pupil's full travel range with
// margin: x in [SCREEN_CENTER_X-20-14, +20+14] = [30,98], y in
// [SCREEN_CENTER_Y-2-14, +2+14] = [16,48]. Used to flush only that strip
// instead of the whole 1024-byte frame on every animated update -- the
// sclera outside it never changes, so it doesn't need resending. This
// keeps each core's transfer short, on top of the two cores already
// running R and L concurrently (see IMPORTANT above).
#define PUPIL_TILE_TX  3
#define PUPIL_TILE_TY  1
#define PUPIL_TILE_TW 10
#define PUPIL_TILE_TH  6

// Pupil motion. X bound is well inside (EYE_W/2 - PUPIL_R) = 28 so the
// pupil never touches the sclera edge. Y is kept small -- this reads as a
// side-to-side glance rather than wandering diagonally.
#define EYE_OFFSET_X_MAX  20
#define EYE_OFFSET_Y_MAX   2
#define EYE_LOOK_SMOOTH  0.3f   // pupil glide rate per tick -- fast but still a glide, not a snap
#define EYE_LOOK_HOLD_MIN_MS  280   // how long a look target is held before picking the next
#define EYE_LOOK_HOLD_MAX_MS  420   // one -- long enough for the glide above to actually finish
#define EYE_FRAME_MS       20   // tick throttle (~50fps cap on the glide)

// U8G2_R0 = no rotation; U8X8_PIN_NONE = no reset pin (these boards don't
// have one). Constructor pin order is (rotation, clock/SCL, data/SDA, reset).
static U8G2_SSD1306_128X64_NONAME_F_SW_I2C eyeR(U8G2_R0, EYE_R_SCL, EYE_R_SDA, U8X8_PIN_NONE);
static U8G2_SSD1306_128X64_NONAME_F_SW_I2C eyeL(U8G2_R0, EYE_L_SCL, EYE_L_SDA, U8X8_PIN_NONE);

// Look positions cycled through in order: left, front (center), right, then
// back down to front, left, front, right... -- a ping-pong through the
// index below, so front is always the midpoint between the two sides
// rather than a random jump.
static const int16_t kLookX[3] = { -EYE_OFFSET_X_MAX, 0, EYE_OFFSET_X_MAX };
static int8_t lookIdx = 1;    // start at front
static int8_t lookDir = 1;

static float eyeOffsetXf = 0, eyeOffsetYf = 0;
static int16_t eyeTargetX = 0, eyeTargetY = 0;
static unsigned long nextLookMs = 0;
static unsigned long lastFrameMs = 0;
static int lastEyeQ = -1000;  // dedup key, avoids redundant redraws

static SemaphoreHandle_t goR, doneR, goL, doneL;

// Draw only -- no I2C. Mutates the display's in-RAM buffer; the flush
// tasks below transmit it separately.
static void drawEye(U8G2_SSD1306_128X64_NONAME_F_SW_I2C &d, int16_t offsetX, int16_t offsetY) {
  const int16_t top = SCREEN_CENTER_Y - EYE_H_OPEN / 2;
  const int16_t x = SCREEN_CENTER_X - EYE_W / 2;
  const uint8_t r = (uint8_t)min((int16_t)(EYE_H_OPEN / 2), (int16_t)(EYE_W / 2));

  d.clearBuffer();
  d.setDrawColor(1);
  d.drawRBox(x, top, EYE_W, EYE_H_OPEN, r);           // sclera -- fixed, never moves
  d.setDrawColor(0);
  d.drawDisc(SCREEN_CENTER_X + offsetX, SCREEN_CENTER_Y + offsetY, PUPIL_R);  // pupil -- the only thing that moves
}

// Pinned one per core (see IMPORTANT above) -- each does nothing but wait
// for "go", flush its own display's pupil-strip tiles, signal "done".
static void eyeFlushTaskR(void *arg) {
  for (;;) {
    xSemaphoreTake(goR, portMAX_DELAY);
    eyeR.updateDisplayArea(PUPIL_TILE_TX, PUPIL_TILE_TY, PUPIL_TILE_TW, PUPIL_TILE_TH);
    xSemaphoreGive(doneR);
  }
}
static void eyeFlushTaskL(void *arg) {
  for (;;) {
    xSemaphoreTake(goL, portMAX_DELAY);
    eyeL.updateDisplayArea(PUPIL_TILE_TX, PUPIL_TILE_TY, PUPIL_TILE_TW, PUPIL_TILE_TH);
    xSemaphoreGive(doneL);
  }
}

static void updateEyes(int16_t offsetX, int16_t offsetY) {
  int q = (offsetX + 100) * 1000 + (offsetY + 100);
  if (q == lastEyeQ) return;
  lastEyeQ = q;

  drawEye(eyeR, offsetX, offsetY);
  drawEye(eyeL, offsetX, offsetY);

  // Kick off both transfers, then wait for both -- they run concurrently
  // on separate cores, so this waits ~max(R,L) instead of R+L.
  xSemaphoreGive(goR);
  xSemaphoreGive(goL);
  xSemaphoreTake(doneR, portMAX_DELAY);
  xSemaphoreTake(doneL, portMAX_DELAY);
}

void eyesBegin() {
  randomSeed(analogRead(34));  // GPIO34 is free/unwired -- floating-pin noise as a seed

  eyeR.begin();
  eyeL.begin();
  Serial.println("Eyes: right/left begin() done (software I2C -- "
                  "no per-panel OK/FAILED check available, verify visually)");

  drawEye(eyeR, 0, 0);
  drawEye(eyeL, 0, 0);
  eyeR.sendBuffer();  // establish the full frame once, directly -- no flush
  eyeL.sendBuffer();  // tasks yet; animated updates only flush the pupil strip

  goR = xSemaphoreCreateBinary();
  doneR = xSemaphoreCreateBinary();
  goL = xSemaphoreCreateBinary();
  doneL = xSemaphoreCreateBinary();
  xTaskCreatePinnedToCore(eyeFlushTaskR, "eyeFlushR", 4096, NULL, 1, NULL, 0);
  xTaskCreatePinnedToCore(eyeFlushTaskL, "eyeFlushL", 4096, NULL, 1, NULL, 1);

  nextLookMs = millis();
}

void eyesTick(unsigned long now) {
  if (now - lastFrameMs < EYE_FRAME_MS) return;
  lastFrameMs = now;

  if (now >= nextLookMs) {
    lookIdx += lookDir;
    if (lookIdx >= 2) { lookIdx = 2; lookDir = -1; }       // hit right -> head back toward front
    else if (lookIdx <= 0) { lookIdx = 0; lookDir = 1; }   // hit left -> head back toward front
    eyeTargetX = kLookX[lookIdx];
    eyeTargetY = (int16_t)random(-EYE_OFFSET_Y_MAX, EYE_OFFSET_Y_MAX + 1);
    nextLookMs = now + random(EYE_LOOK_HOLD_MIN_MS, EYE_LOOK_HOLD_MAX_MS + 1);
  }
  eyeOffsetXf += (eyeTargetX - eyeOffsetXf) * EYE_LOOK_SMOOTH;
  eyeOffsetYf += (eyeTargetY - eyeOffsetYf) * EYE_LOOK_SMOOTH;
  updateEyes((int16_t)lroundf(eyeOffsetXf), (int16_t)lroundf(eyeOffsetYf));
}
