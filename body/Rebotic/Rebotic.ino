// Rebotic — ESP32 arm controller.
// Scope: WiFi + MQTT transport (net.*) driving the 6-servo arm gesture
// engine (arms.*) per specs/07-arms-motion.md. Neck (specs/06) is not
// built onto this board yet — GPIO 14/27 stay reserved for it.
//
// This file is only setup()/loop() wiring; see net.h and arms.h for the
// actual logic and body/specs/02-firmware.md for the protocol contract.
#include "config.h"
#include "net.h"
#include "arms.h"
#include <esp_task_wdt.h>

static bool idleForced = false;

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
  esp_task_wdt_add(NULL);  // subscribe this (loop) task

  netBegin(armsOnCommand);
  armsBegin();
  Serial.println("Rebotic arms -- ready");
}

void loop() {
  esp_task_wdt_reset();
  netLoop();

  unsigned long now = millis();
  if (now - netLastCmdMillis() > NO_CMD_TIMEOUT_MS) {
    if (!idleForced) {
      armsOnCommand("state", "idle");  // laptop probably restarted / link dropped
      idleForced = true;
    }
  } else {
    idleForced = false;
  }

  armsTick(now);
}
