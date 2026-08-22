// Rebotic — arm servo / gesture engine (spec 07).
// Owns all 6 arm servos (shoulder lift + rotate + elbow, per side), the
// state-driven idle/attend/thinking/speak animations, and the one-shot
// "shrug" gesture. Never blocks — armsTick() is a pure function of millis().
#pragma once
#include <Arduino.h>

// Call once from setup(), after netBegin(). Attaches all 6 servos and eases
// them to their neutral pose over ~1s before returning.
void armsBegin();

// Call every loop() iteration. Drives whatever animation is current
// (state breathe/sway or an in-flight gesture) and writes all 6 servos.
// Also owns the "5s no motion demand -> detach" safety timer.
void armsTick(unsigned long now);

// Feed MQTT rebotic/cmd messages here. Handles t == "state" | "gest";
// silently ignores "amp" (no jaw on this board) and anything unrecognized.
void armsOnCommand(const char* t, const char* v);
