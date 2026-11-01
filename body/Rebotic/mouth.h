// Rebotic — mouth (1x OLED). See mouth.cpp for wiring/rendering details.
#pragma once
#include <Arduino.h>

// Call once from setup(). Draws the mouth's resting (closed) shape.
void mouthBegin();

// Call from the shared face animation task (see face.cpp) -- NOT its own
// task. Internally throttled; cheap to call often. While speaking (see
// mouthSetSpeaking) and the last amp value is still fresh, draws the
// mouth's gap as a function of that amp value; otherwise holds closed.
void mouthTick(unsigned long now);

// Fed from faceOnCommand() on every "state" message. true only for
// state=="speak" -- every other state means the mouth should be closed
// regardless of whatever amp value is still buffered.
void mouthSetSpeaking(bool speaking);

// Fed from faceOnCommand() on every "amp" message: the real 0-255
// RMS-derived mouth amplitude voice.py publishes at 40Hz while actually
// speaking. Also stamps when it arrived, so mouthTick can tell a fresh amp
// value from a stale one (see MOUTH_AMP_STALE_MS in mouth.cpp).
void mouthSetAmp(int amp, unsigned long now);
