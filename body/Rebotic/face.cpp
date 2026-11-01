// Rebotic — face. See face.h. Thin orchestrator: eyes and mouth are each
// their own module (eyes.*/mouth.*) with independent wiring and rendering,
// but this file owns the ONE shared animation task that drives both.
//
// IMPORTANT -- why one shared task and not one task per panel: an earlier
// version gave eyes and mouth each their own independent FreeRTOS task,
// both bit-banging separate software-I2C buses concurrently. Bit-banged
// I2C isn't safe under arbitrary preemption -- one task's transaction can
// get interrupted mid-clock-pulse by the other task being scheduled in,
// which was corrupting frames on one or both displays ("mixing everything
// up"). This task calls eyesTick() then mouthTick() sequentially every
// iteration, so only one display is ever being written to at a time -- no
// two bit-bang transactions can overlap.
//
// This task is started at the end of faceBegin(), instead of being driven
// from loop(). Reason: Rebotic.ino's setup() calls netBegin() right after
// faceBegin(), and netBegin() blocks -- often for a long time (WiFiManager
// retrying a saved network, then falling back to its own "Rebotic-Setup"
// config-portal AP, which blocks indefinitely until someone configures
// it). loop() never runs until netBegin() returns, so animation driven
// from loop() would just sit frozen on one frame for as long as WiFi setup
// takes. A dedicated task sidesteps that entirely: it starts animating
// immediately in faceBegin(), independent of what setup()/loop() is doing.
#include "face.h"
#include "eyes.h"
#include "mouth.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <string.h>
#include <stdlib.h>

static void faceAnimTaskFn(void *arg) {
  Serial.println("Face: animation task started");
  for (;;) {
    unsigned long now = millis();
    eyesTick(now);
    mouthTick(now);
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

void faceBegin() {
  eyesBegin();
  mouthBegin();

  xTaskCreate(faceAnimTaskFn, "faceAnim", 4096, NULL, 1, NULL);
}

void faceTick(unsigned long now) {
  // Eyes and mouth animate on the shared task above -- nothing to do here.
}

void faceOnCommand(const char *t, const char *v) {
  if (strcmp(t, "state") == 0) {
    mouthSetSpeaking(strcmp(v, "speak") == 0);
  } else if (strcmp(t, "amp") == 0) {
    mouthSetAmp(atoi(v), millis());
  }
  // Eyes always animate on their own -- state/amp protocol intentionally
  // ignored here for now (see PROJECT_CONTEXT.md §6.8 for the planned
  // follow-up pass). "gest" is arms-only, ignored here too.
}
