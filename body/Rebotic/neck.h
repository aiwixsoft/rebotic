// Rebotic — neck servo engine (strict port of sholderNeck.ino's servo3).
// Same pin (4), same 0-20deg range, same cosine easing, same nod gesture.
// Only 2 states, per explicit request: idle=0, speak=10 (attend/thinking
// removed project-wide, including the Python side that used to send them).
#pragma once
#include <Arduino.h>

void neckBegin();   // call once from setup()
void neckTick(unsigned long now);   // call every loop() iteration

// Feed MQTT rebotic/cmd messages here. t == "state" (idle/speak only) and
// t == "gest" with v == "nod" handled. "tilt"/"amp"/anything else ignored
// (tilt is arms.cpp's).
void neckOnCommand(const char* t, const char* v);
