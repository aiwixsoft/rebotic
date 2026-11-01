// Rebotic — arm servo engine (spec 07).
// 5 servos: left/right shoulder-lift + shoulder-rotate, right elbow (left
// elbow repurposed as the neck pan servo -- see neck.h/config.h).
// Shoulder-rotate is hard-frozen (staticHold, arms.cpp) pending an open
// jitter investigation. Shoulder-lift + elbow sway gently while
// state == "speak"; idle/attend/thinking hold everything at rest.
// Non-blocking -- armsTick() is a pure function of millis().
#pragma once
#include <Arduino.h>

void armsBegin();   // call once from setup(), after netBegin()
void armsTick(unsigned long now);   // call every loop() iteration

// Feed MQTT rebotic/cmd messages here. t == "state" | "zero" handled;
// "gest"/"amp"/anything else ignored.
//
// {"t":"zero","v":"on"} is a bring-up override: holds all 5 servos at raw
// 0 deg (elbow floored at 90, see arms.cpp) ignoring calibration/limits,
// until "off" or any state command. Not subject to the idle-detach timer.
void armsOnCommand(const char* t, const char* v);
