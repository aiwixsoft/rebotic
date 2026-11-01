// Rebotic — mouth (1x OLED). See mouth.h.
//
// Wiring: 4-pin I2C SSD1306 board (GND, VDD, SCK, SDA), same fixed I2C
// address as the eye panels -- so the mouth gets its OWN dedicated pair of
// SDA/SCL pins, no shared wiring, no address conflict: SDA=GPIO5, SCL=GPIO17.
//
// IMPORTANT -- why U8g2 and not Adafruit_SSD1306 here: the ESP32 only has
// 2 *hardware* I2C peripherals. An earlier version of this codebase drove
// all 3 displays (2 eyes + mouth) by having two of them share one hardware
// peripheral, re-pointing it to different pins at runtime (Wire1.begin(sda,
// scl)) right before talking to whichever panel needed it. That looked fine
// in testing (both eyes worked) but the mouth panel silently broke --
// Serial showed `i2c_master_transmit failed: [259] ESP_ERR_INVALID_STATE`
// on every write. Cause: this core's I2C-NG driver tears down/rebuilds the
// bus's internal handle every time begin() is called with different pins,
// which invalidates the *other* display's already-attached device handle --
// so whichever display initialized first silently stops working the moment
// the other one's pins get selected, and it doesn't come back. U8g2's
// software-I2C mode bit-bangs each display's pins directly with plain
// digitalWrite() -- no shared hardware peripheral, no handle to invalidate,
// so fully independent instances (one per panel, see eyes.cpp too) are safe.
//
// Library needed (Arduino IDE -> Tools -> Manage Libraries): "U8g2"
//
// Rendering: two curved lip strokes (parabolic arcs) that always meet at
// both corners and bulge apart at the center by a `gap` -- bigger gap =
// more open. While speaking (mouthSetSpeaking(true)) and the last amp
// value is still fresh, gap tracks the real 0-255 amp value voice.py
// publishes over MQTT at 40Hz (sqrt-scaled -- see mouthTick -- so
// ordinary-volume speech still visibly opens the mouth, not just loud
// syllables). The moment speaking stops, or amp updates go stale (a broker
// hiccup mid-speech), gap glides back to MOUTH_GAP_MIN (closed) instead of
// freezing wherever it was. This replaced an earlier version where the
// mouth free-ran on a random walk at all times, whether or not the robot
// was actually speaking -- that looked "alive" but never actually matched
// the agent, which is the entire reason this was wired up to amp instead.
//
// IMPORTANT -- mouthTick() is driven by the single shared animation task in
// face.cpp, NOT its own task. An earlier version gave the mouth (and eyes)
// each their own independent FreeRTOS task, both bit-banging separate
// software-I2C buses concurrently. Bit-banged I2C isn't safe under
// arbitrary preemption: one task's transaction can get interrupted
// mid-clock-pulse by the other task being scheduled in, which was
// corrupting frames on one or both displays ("mixing everything up"). See
// face.cpp for why that shared task exists in the first place (it still
// must not depend on netBegin()/loop() ever returning). Nothing else
// touches mouthDisp, so no locking is needed.
#include "mouth.h"
#include <U8g2lib.h>
#include <math.h>

#define MOUTH_SDA  5
#define MOUTH_SCL 17

#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
#define SCREEN_CENTER_X (SCREEN_WIDTH / 2)
#define SCREEN_CENTER_Y (SCREEN_HEIGHT / 2)

// Mouth geometry -- two curved lip strokes (parabolic arcs), always meeting
// at both corners (SCREEN_CENTER_X +/- MOUTH_W/2), bulging apart at the
// center by `gap`.
#define MOUTH_W         126    // near-edge-to-edge on the 128px-wide panel
#define LIP_STROKE        8    // thickness of each curved lip line, px -- bold
// MOUTH_GAP_MIN is deliberately > LIP_STROKE: the visible opening between
// the two lips' inner edges at the center is (gap - LIP_STROKE), so a gap
// at or below LIP_STROKE would merge the two lips into one solid blob.
// MOUTH_GAP_MIN=16 keeps a real ~8px opening even fully closed -- reads as
// a resting/neutral mouth, not a flat line.
#define MOUTH_GAP_MIN    16
#define MOUTH_GAP_MAX    34    // wide open, at amp==255

// Amp -> gap mapping/motion.
#define MOUTH_AMP_SMOOTH   0.4f   // fraction of remaining distance-to-target covered per
                                  // tick -- amp itself only updates ~every 25ms (voice.py's
                                  // 40Hz publish rate) but this tick runs far faster, so
                                  // without some smoothing the mouth would look like a
                                  // stepped square wave between updates instead of a glide.
#define MOUTH_AMP_STALE_MS  300   // state==speak but no amp update this recently (broker
                                  // hiccup) -> treat as silence and glide closed, rather
                                  // than freezing wherever the mouth last was -- same
                                  // "never leave it stuck open" principle voice.py applies
                                  // on its own side for its own amp publish.
#define FRAME_MS          1   // tick throttle -- effectively as fast as the mouth panel's
                              // bit-banged I2C sendBuffer() can physically keep up with

// U8G2_R0 = no rotation; U8X8_PIN_NONE = no reset pin (this board doesn't
// have one). Constructor pin order is (rotation, clock/SCL, data/SDA, reset).
static U8G2_SSD1306_128X64_NONAME_F_SW_I2C mouthDisp(U8G2_R0, MOUTH_SCL, MOUTH_SDA, U8X8_PIN_NONE);

static float mouthGapf = MOUTH_GAP_MIN;
static int lastMouthQ = -1;  // dedup key, avoids redundant redraws
static unsigned long lastFrameMs = 0;

static bool isSpeaking = false;
static int lastAmp = 0;
static unsigned long lastAmpMillis = 0;

// Both lips are the same parabola, mirrored: offset(t) = amp*(1 - t*t) where
// t goes from -1 (left corner) to +1 (right corner). offset is 0 at both
// corners -- the two lips meet there by construction, however wide `gap`
// is -- and peaks at the center, so the mouth reads as a curved lens shape
// rather than two flat bars floating apart.
static void renderMouth(int16_t gap) {
  mouthDisp.clearBuffer();
  mouthDisp.setDrawColor(1);

  const int16_t halfW = MOUTH_W / 2;
  const float amp = gap / 2.0f;
  for (int16_t dx = -halfW; dx <= halfW; dx++) {
    const float t = (float)dx / halfW;
    const int16_t offset = (int16_t)lroundf(amp * (1.0f - t * t));
    const int16_t x = SCREEN_CENTER_X + dx;
    mouthDisp.drawVLine(x, SCREEN_CENTER_Y - offset - LIP_STROKE / 2, LIP_STROKE);  // upper lip
    mouthDisp.drawVLine(x, SCREEN_CENTER_Y + offset - LIP_STROKE / 2, LIP_STROKE);  // lower lip
  }

  mouthDisp.sendBuffer();
}

static void updateMouth(int16_t gap) {
  if (gap == lastMouthQ) return;
  lastMouthQ = gap;
  renderMouth(gap);
}

void mouthBegin() {
  mouthDisp.begin();
  Serial.println("Mouth: begin() done (software I2C -- "
                  "no per-panel OK/FAILED check available, verify visually)");

  updateMouth(MOUTH_GAP_MIN);
}

void mouthSetSpeaking(bool speaking) {
  isSpeaking = speaking;
}

void mouthSetAmp(int amp, unsigned long now) {
  lastAmp = constrain(amp, 0, 255);
  lastAmpMillis = now;
}

void mouthTick(unsigned long now) {
  if (now - lastFrameMs < FRAME_MS) return;
  lastFrameMs = now;

  int16_t target;
  if (isSpeaking && (now - lastAmpMillis) < MOUTH_AMP_STALE_MS) {
    // sqrt-scaled: RMS-derived amp sits low for ordinary speech and only
    // spikes on loud syllables -- a straight linear map would leave the
    // mouth looking almost shut for most of a normal sentence. sqrt lifts
    // the low end so ordinary speech still visibly opens it.
    float norm = sqrtf(lastAmp / 255.0f);
    target = (int16_t)lroundf(MOUTH_GAP_MIN + norm * (MOUTH_GAP_MAX - MOUTH_GAP_MIN));
  } else {
    target = MOUTH_GAP_MIN;  // not speaking, or amp gone stale -> closed
  }

  mouthGapf += (target - mouthGapf) * MOUTH_AMP_SMOOTH;
  updateMouth((int16_t)lroundf(mouthGapf));
}
