// Rebotic — neck servo engine (spec 06, PAN ONLY).
// Owns the single neck-pan servo (GPIO 4, repurposed from the removed left
// elbow -- see config.h and arms.h). Spec 06 specced pan+tilt on 2 servos;
// only pan is actually built here (no tilt servo/hardware exists), so every
// tilt-only spec 06 behavior (the "nod"/"tilt" gestures, thinking's tilt
// oscillation) has no pan equivalent and is a silent no-op in this module.
#pragma once
#include <Arduino.h>

// Call once from setup(). Attaches the pan servo and eases it to center
// over ~1s before returning.
void neckBegin();

// Call every loop() iteration. Pure function of millis(), same non-blocking
// pattern as armsTick() -- also owns its own 5s no-motion-demand -> detach
// safety timer, independent of arms.cpp's.
void neckTick(unsigned long now);

// Feed MQTT rebotic/cmd messages here. Handles t == "state" (same 4 values
// as arms: idle/attend/thinking/speak). t == "gest" with v == "nod" or
// "tilt" is accepted without error but is a silent no-op -- both are
// tilt-axis motions and there's no tilt servo to drive them with. t ==
// "amp" or anything else: ignored, same as arms.cpp.
void neckOnCommand(const char* t, const char* v);
