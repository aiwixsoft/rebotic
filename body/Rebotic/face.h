// Rebotic — face. Thin orchestrator over eyes.* and mouth.* (each panel's
// wiring/rendering lives in its own module -- see eyes.h and mouth.h; this
// file owns the one shared task that animates both, see face.cpp for why
// it must be one task, not one per panel). Eyes' sclera is static, pupil
// wanders, independent of the state/amp protocol. The mouth DOES react to
// it: faceOnCommand() forwards "state" (speaking or not) and "amp" (real
// 0-255 mouth amplitude) through to mouth.cpp, so the mouth's shape tracks
// actual agent speech instead of animating on its own.
#pragma once
#include <Arduino.h>

// Call once from setup(), before netBegin() (netBegin blocks on WiFi/the
// captive portal, and the face should be visible immediately regardless).
void faceBegin();

// Call every loop() iteration. No-op -- eyes and mouth animate on the one
// shared background task started in faceBegin(), independent of loop().
void faceTick(unsigned long now);

// Feed the same rebotic/cmd {t, v} pairs arms.* gets. Forwards "state" and
// "amp" to mouth.cpp (see mouth.h) so the mouth tracks real agent speech;
// eyes still ignore everything here and animate independently for now.
void faceOnCommand(const char* t, const char* v);
