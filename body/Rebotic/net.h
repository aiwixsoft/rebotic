// Rebotic — WiFi + MQTT transport (spec 02).
// Non-blocking connect/reconnect for both layers; parses `rebotic/cmd` JSON
// and hands {t, v} pairs to a callback. No delay() anywhere in here.
#pragma once
#include <Arduino.h>

typedef void (*NetCommandHandler)(const char* t, const char* v);

// Call once from setup(). Blocks for the *initial* WiFi join only (there's
// nothing useful to do yet anyway); every reconnect after that is
// non-blocking, gated by netLoop().
void netBegin(NetCommandHandler handler);

// Call every loop() iteration. Services WiFi reconnect, MQTT reconnect, and
// MQTT's internal loop() (which delivers subscribed messages via the
// handler passed to netBegin). Cheap when already connected.
void netLoop();

// millis() timestamp of the last successfully parsed rebotic/cmd message.
// Used by main .ino for the "no cmd for 10s -> force idle" rule and by the
// arms module for the "5s no motion demand -> detach" rule.
unsigned long netLastCmdMillis();

bool netIsConnected();  // WiFi + MQTT both up
