// Rebotic — ESP32 arm + neck controller.
// Scope: WiFi + MQTT transport (net.*) driving the 5-servo arm engine
// (arms.*, specs/07 -- shoulder-rotate hard-frozen pending an open jitter
// investigation, shoulder-lift + elbow free to sway gently while speaking)
// and the 1-servo neck-pan engine (neck.*, specs/06, pan-only -- the left
// elbow was removed and repurposed as the neck pan servo, see
// config.h/arms.h/neck.h). 6 servos total are driven by this firmware.
//
// This file is only setup()/loop() wiring; see net.h/arms.h/neck.h for the
// actual logic and body/specs/02-firmware.md for the protocol contract.
#include "config.h"
#include "net.h"
#include "arms.h"
#include "neck.h"
#include "face.h"
#include <esp_task_wdt.h>

static bool idleForced = false;

// arms.*, neck.*, and face.* all need every rebotic/cmd message -- net.h
// only carries one handler, so fan it out here rather than complicating net.*.
static void onNetCommand(const char* t, const char* v) {
  armsOnCommand(t, v);
  neckOnCommand(t, v);
  faceOnCommand(t, v);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("Rebotic arms -- booting");

  esp_task_wdt_config_t wdtConfig = {
    .timeout_ms = WDT_TIMEOUT_S * 1000,
    .idle_core_mask = 0,
    .trigger_panic = true,
  };
  if (esp_task_wdt_init(&wdtConfig) != ESP_OK) {
    esp_task_wdt_reconfigure(&wdtConfig);  // core already inited its own TWDT -> retarget it
  }
  // NOT esp_task_wdt_add(NULL) yet -- netBegin() below blocks on WiFi/portal
  // (BOOT-hold window + WiFiManager join + mDNS + broker resolve), which
  // routinely runs past WDT_TIMEOUT_S with nothing feeding the watchdog.
  // Subscribing this task before that finished was panicking (rebooting)
  // the board mid-connect, over and over -- which looked like the face
  // appearing then vanishing every few seconds. Subscribe only once we're
  // about to enter loop(), where esp_task_wdt_reset() actually runs.

  faceBegin();  // before netBegin(): netBegin blocks on WiFi/portal, and we
                // want the face visible immediately at boot either way.
  netBegin(onNetCommand);
  armsBegin();
  neckBegin();

  esp_task_wdt_add(NULL);  // subscribe this (loop) task, now that setup()'s blocking work is done
  Serial.println("Rebotic arms -- ready");
}

void loop() {
  esp_task_wdt_reset();
  netLoop();

  unsigned long now = millis();
  if (now - netLastCmdMillis() > NO_CMD_TIMEOUT_MS) {
    if (!idleForced) {
      onNetCommand("state", "idle");  // laptop probably restarted / link dropped
      idleForced = true;
    }
  } else {
    idleForced = false;
  }

  armsTick(now);
  neckTick(now);
  faceTick(now);
}
