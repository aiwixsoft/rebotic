// Rebotic — arm servo engine (strict port of sholderNeck.ino's shoulder
// pair). servo1 = R_SHOULDER_ROTATE (pin 13), servo2 = R_SHOULDER_LIFT
// (pin 32) -- that file's exact pins, exact pose/gesture constants and
// cosine easing, reproduced non-blocking (tracked by elapsed time per tick
// instead of a delay() loop) so MQTT/watchdog never stall. Gesture/speak
// stay within that file's 90-180deg range; idle targets 0 on both servos
// (below that range) -- per explicit request. This un-freezes shoulder-
// rotate (previously staticHold, pending an unresolved jitter
// investigation) -- per explicit request.
// L_SHOULDER_LIFT, L_SHOULDER_ROTATE, R_ELBOW aren't part of that file's
// 3-servo model; they hold at rest, undriven.
#pragma once
#include <Arduino.h>

void armsBegin();   // call once from setup(), after netBegin()
void armsTick(unsigned long now);   // call every loop() iteration

// Feed MQTT rebotic/cmd messages here. t == "state" | "gest" | "zero"
// handled -- state is idle/speak only (attend/thinking removed project-
// wide, per explicit request); "gest" only for "tilt"/"shrug" ("nod" is
// neck.cpp's); "amp"/anything else ignored.
//
// {"t":"zero","v":"on"} is a bring-up override: holds servo1/servo2 and the
// 3 undriven joints at raw 0 deg (elbow floored at 90) ignoring
// calibration/limits, until "off" or any state command.
void armsOnCommand(const char* t, const char* v);
