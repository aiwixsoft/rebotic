// Rebotic — build-time configuration.
// Edit per build: WiFi creds, broker address, pin/servo calibration.
#pragma once

// ─── WiFi (dynamic — no hardcoded network) ─────────────────────────────────
// Credentials are entered at runtime via WiFiManager's captive portal, not
// compiled in. First boot (or after a BOOT-button reset) with no saved
// network: ESP32 opens its own AP named below (open, no password) — connect
// a phone/laptop to it, a setup page pops up, enter any WiFi's SSID+password
// there. Saved to flash; reused automatically on every boot after that.
// To switch networks later: hold the BOOT button (GPIO0) while powering on
// or pressing EN/reset — that wipes the saved network and reopens the portal.
#define WIFI_PORTAL_AP_NAME "Rebotic-Setup"
#define WIFI_PORTAL_TIMEOUT_S 600   // give up and reboot to retry if nobody configures it
// If the saved network stays unreachable this long (e.g. you moved to a new
// place), the portal reopens automatically — no BOOT-button press needed.
#define WIFI_AUTO_PORTAL_MS (3UL * 60UL * 1000UL)

// ─── MQTT ───────────────────────────────────────────────────────────────────
// Broker = the laptop running mosquitto (spec 02). Found by mDNS hostname
// instead of a hardcoded IP, so it keeps working across any WiFi network as
// long as the laptop and ESP32 are on the same one. This is the laptop's
// hostname (`hostname` command, no ".local" suffix) — currently "aiwix".
#define MQTT_BROKER_MDNS_NAME "aiwix"
#define MQTT_BROKER_PORT 1883
#define MQTT_CLIENT_ID   "rebotic-arms"
#define MQTT_TOPIC_CMD   "rebotic/cmd"   // subscribe: laptop -> body

// ─── Arm servo pins (spec 07) ───────────────────────────────────────────────
// Reserved, do not reuse when the neck (spec 06) is merged onto this board:
// GPIO 14 (neck pan), GPIO 27 (neck tilt).
#define PIN_L_SHOULDER_LIFT   26
#define PIN_L_SHOULDER_ROTATE 33
#define PIN_L_ELBOW           4
#define PIN_R_SHOULDER_LIFT   32
#define PIN_R_SHOULDER_ROTATE 13
#define PIN_R_ELBOW           16

// ─── Servo calibration (placeholders — set these during bring-up, see
// specs/07-arms-motion.md "Bring-up") ───────────────────────────────────────
// Base = resting/idle neutral angle. Min/Max = safe mechanical travel.
struct ServoCal {
  uint8_t base;
  uint8_t minAngle;
  uint8_t maxAngle;
  bool    mirror;   // true: angle is mapped as (180 - angle) before writing
};

// Right arm mirrors the left physically; if a joint moves the wrong
// direction on first power-up, flip its `mirror` flag here rather than
// rewiring.
static const ServoCal CAL_L_SHOULDER_LIFT   = {90, 40, 150, false};
static const ServoCal CAL_L_SHOULDER_ROTATE = {90, 30, 150, false};
static const ServoCal CAL_L_ELBOW           = {100, 60, 150, false};
static const ServoCal CAL_R_SHOULDER_LIFT   = {90, 40, 150, false};
static const ServoCal CAL_R_SHOULDER_ROTATE = {90, 30, 150, true};
static const ServoCal CAL_R_ELBOW           = {100, 60, 150, true};

// ─── Timing / fail-safe (spec 02) ──────────────────────────────────────────
#define NO_CMD_TIMEOUT_MS    10000  // no cmd this long -> force state=idle
#define SERVO_DETACH_IDLE_MS 5000   // no cmd this long -> detach all servos
#define WDT_TIMEOUT_S        8      // hardware watchdog
#define WIFI_RETRY_MIN_MS    2000
#define WIFI_RETRY_MAX_MS    30000
#define MQTT_RETRY_MS        2000
