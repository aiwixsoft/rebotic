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
// Left elbow repurposed as neck pan (PIN_NECK_PAN below). Shoulder-rotate
// is hard-frozen in firmware pending an open jitter investigation (arms.cpp).
// GPIO 27 reserved for a possible future neck-tilt servo (spec 06; only
// pan is actually built).
#define PIN_L_SHOULDER_LIFT   26
#define PIN_L_SHOULDER_ROTATE 33
#define PIN_R_SHOULDER_LIFT   32
#define PIN_R_SHOULDER_ROTATE 13
#define PIN_R_ELBOW           16

// ─── Neck servo pin (spec 06, pan-only) ────────────────────────────────────
// Spec 06 originally put pan on GPIO 14; instead this reuses the removed
// L_ELBOW's pin (GPIO 4) so no ESP32-side rewiring is needed -- just move
// the physical servo from the elbow mount to the neck pan mount.
#define PIN_NECK_PAN 4

// ─── Servo calibration (placeholders — set these during bring-up, see
// specs/07-arms-motion.md "Bring-up") ───────────────────────────────────────
// Base = resting/idle neutral angle. Min/Max = safe mechanical travel.
struct ServoCal {
  uint8_t base;
  uint8_t minAngle;
  uint8_t maxAngle;
  bool    mirror;   // true: angle is mapped as (180 - angle) before writing
};

// Right arm mirrors the left; flip `mirror` here, not by rewiring, if a
// joint moves the wrong way. Lift base is 12 (not 0) to stay off the
// mechanical floor -- sitting a loaded joint right at a hard stop buzzes.
static const ServoCal CAL_L_SHOULDER_LIFT   = {12, 0, 150, false};
static const ServoCal CAL_L_SHOULDER_ROTATE = {90, 30, 150, false};
static const ServoCal CAL_R_SHOULDER_LIFT   = {12, 0, 150, false};
static const ServoCal CAL_R_SHOULDER_ROTATE = {90, 30, 150, true};
static const ServoCal CAL_R_ELBOW           = {100, 90, 150, true};

// Neck pan -- placeholder like the others above, calibrate during bring-up.
// base=90 is dead-center/looking-forward; +-60deg of travel each side.
static const ServoCal CAL_NECK_PAN          = {90, 30, 150, false};

// ─── Timing / fail-safe (spec 02) ──────────────────────────────────────────
#define NO_CMD_TIMEOUT_MS    10000  // no cmd this long -> force state=idle
#define SERVO_DETACH_IDLE_MS 5000   // no cmd this long -> detach all servos
#define WDT_TIMEOUT_S        8      // hardware watchdog
#define WIFI_RETRY_MIN_MS    2000
#define WIFI_RETRY_MAX_MS    30000
#define MQTT_RETRY_MS        2000
