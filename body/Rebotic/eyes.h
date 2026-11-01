// Rebotic — eyes (2x OLED). See eyes.cpp for wiring/rendering details.
#pragma once
#include <Arduino.h>

// Call once from setup(). Draws both eyes (fixed sclera + centered pupil).
void eyesBegin();

// Call from the shared face animation task (see face.cpp) -- NOT its own
// task. Internally throttled; cheap to call often. Drives the pupil's
// idle-look wander as a pure function of millis(); sclera never changes.
void eyesTick(unsigned long now);
