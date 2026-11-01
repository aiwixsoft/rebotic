#include "net.h"
#include "config.h"
#include <WiFi.h>
#include <WiFiManager.h>
#include <ESPmDNS.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

static WiFiClient wifiClient;
static PubSubClient mqtt(wifiClient);
static NetCommandHandler onCommand = nullptr;
static unsigned long lastCmdMillis = 0;

// WiFi reconnect backoff state
static unsigned long wifiNextAttempt = 0;
static unsigned long wifiBackoffMs = WIFI_RETRY_MIN_MS;
static unsigned long wifiLastConnectedMillis = 0;
static bool autoPortalArmed = true;

// MQTT reconnect throttle
static unsigned long mqttNextAttempt = 0;

// Broker IP resolved via mDNS (config.h has a hostname, not an IP — it
// changes per network, so we look it up instead of hardcoding it).
static IPAddress mqttBrokerIp;
static bool mqttBrokerIpValid = false;

static bool resolveBrokerIp(uint32_t timeoutMs) {
  IPAddress ip = MDNS.queryHost(MQTT_BROKER_MDNS_NAME, timeoutMs);
  if (ip == IPAddress((uint32_t)0)) return false;
  mqttBrokerIp = ip;
  mqttBrokerIpValid = true;
  Serial.print("mDNS: broker ");
  Serial.print(MQTT_BROKER_MDNS_NAME);
  Serial.print(".local -> ");
  Serial.println(mqttBrokerIp);
  return true;
}

static void mqttCallback(char* topic, byte* payload, unsigned int length) {
  JsonDocument doc;  // ArduinoJson v7 — auto-sized, no capacity template param
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) return;                       // malformed -> ignore silently
  const char* t = doc["t"];
  if (!t) return;                        // missing field -> ignore silently

  // "v" arrives as a JSON string for state/gest ("idle", "shrug", ...) but
  // as a JSON *number* for amp (0-255). ArduinoJson's as<const char*>()
  // only returns non-null for an actual stored string -- for a number it's
  // NULL, which used to make every amp message silently fail the old
  // `if (!t || !v) return` check before it ever reached onCommand(). Handle
  // both shapes here so every handler downstream can keep treating v as a
  // plain C string regardless of which JSON type it arrived as.
  char vBuf[16];
  const char* v;
  if (doc["v"].is<const char*>()) {
    v = doc["v"].as<const char*>();
  } else if (doc["v"].is<int>()) {
    snprintf(vBuf, sizeof(vBuf), "%d", doc["v"].as<int>());
    v = vBuf;
  } else {
    return;                              // missing/unsupported v type -> ignore silently
  }

  lastCmdMillis = millis();
  if (onCommand) onCommand(t, v);
}

static void wifiConnectBlocking() {
  // BOOT button (GPIO0) held at boot -> forget the saved network and reopen
  // the captive portal, so switching WiFi never needs a re-flash. Sampled
  // over a full 2.5s window (not a one-shot check) so it's actually
  // physically possible for a human to hit by hand after pressing reset.
  pinMode(0, INPUT_PULLUP);
  Serial.println("WiFi: hold BOOT now for 'Rebotic-Setup' (2.5s window)...");
  const unsigned long HOLD_WINDOW_MS = 2500;
  unsigned long windowStart = millis();
  bool bootHeld = true;
  while (millis() - windowStart < HOLD_WINDOW_MS) {
    if (digitalRead(0) != LOW) { bootHeld = false; break; }
    delay(20);
  }
  if (bootHeld) {
    Serial.println("WiFi: BOOT held -> forgetting saved network");
    WiFiManager forgetWm;
    forgetWm.resetSettings();
  }

  WiFiManager wm;
  wm.setConfigPortalTimeout(WIFI_PORTAL_TIMEOUT_S);
  Serial.println("WiFi: connecting (or opening 'Rebotic-Setup' portal if no saved network)...");
  if (!wm.autoConnect(WIFI_PORTAL_AP_NAME)) {
    Serial.println("WiFi: portal timed out with no config, rebooting to retry");
    ESP.restart();
  }
  Serial.print("WiFi: connected, IP ");
  Serial.println(WiFi.localIP());
  wifiBackoffMs = WIFI_RETRY_MIN_MS;

  if (!MDNS.begin("rebotic-arms")) {
    Serial.println("mDNS: init failed");
  }
  resolveBrokerIp(3000);  // best-effort; non-blocking loop retries if this misses
  wifiLastConnectedMillis = millis();
}

static void wifiServiceNonBlocking(unsigned long now) {
  if (WiFi.status() == WL_CONNECTED) {
    wifiLastConnectedMillis = now;
    autoPortalArmed = true;
    return;
  }

  // Disconnected too long -> the saved network probably isn't in range
  // anymore (moved to a new place). Open the portal ourselves instead of
  // waiting for a BOOT-button press. Blocking, but nothing useful can
  // happen (no WiFi = no MQTT = no commands) while this runs anyway.
  if (autoPortalArmed && now - wifiLastConnectedMillis > WIFI_AUTO_PORTAL_MS) {
    Serial.println("WiFi: disconnected too long -> auto-opening 'Rebotic-Setup' portal");
    WiFiManager wm;
    wm.setConfigPortalTimeout(WIFI_PORTAL_TIMEOUT_S);
    if (wm.startConfigPortal(WIFI_PORTAL_AP_NAME)) {
      Serial.print("WiFi: connected via auto-portal, IP ");
      Serial.println(WiFi.localIP());
      mqttBrokerIpValid = false;  // network changed -> re-resolve broker
    } else {
      Serial.println("WiFi: auto-portal timed out, resuming background retries");
    }
    autoPortalArmed = false;      // don't reopen immediately; wait a full cooldown
    wifiLastConnectedMillis = now;
    return;
  }

  if (now < wifiNextAttempt) return;

  Serial.println("WiFi: reconnecting...");
  WiFi.reconnect();  // reuses the network WiFiManager saved to flash
  wifiNextAttempt = now + wifiBackoffMs;
  wifiBackoffMs = min(wifiBackoffMs * 2, (unsigned long)WIFI_RETRY_MAX_MS);
}

static void mqttServiceNonBlocking(unsigned long now) {
  if (WiFi.status() != WL_CONNECTED) return;
  if (mqtt.connected()) {
    mqtt.loop();
    return;
  }
  if (now < mqttNextAttempt) return;

  if (!mqttBrokerIpValid && !resolveBrokerIp(1000)) {
    Serial.print("mDNS: couldn't find ");
    Serial.print(MQTT_BROKER_MDNS_NAME);
    Serial.println(".local yet, will retry");
    mqttNextAttempt = now + MQTT_RETRY_MS;
    return;
  }

  Serial.print("MQTT: connecting to ");
  Serial.print(mqttBrokerIp);
  Serial.println(" ...");
  mqtt.setServer(mqttBrokerIp, MQTT_BROKER_PORT);
  if (mqtt.connect(MQTT_CLIENT_ID)) {
    Serial.println("MQTT: connected");
    mqtt.subscribe(MQTT_TOPIC_CMD);
  } else {
    Serial.print("MQTT: connect failed, rc=");
    Serial.println(mqtt.state());
    mqttBrokerIpValid = false;  // IP may be stale (DHCP renewal etc.) -> re-resolve next try
  }
  mqttNextAttempt = now + MQTT_RETRY_MS;
}

void netBegin(NetCommandHandler handler) {
  onCommand = handler;
  wifiConnectBlocking();

  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(256);

  lastCmdMillis = millis();  // don't let boot look like a stale connection
}

void netLoop() {
  unsigned long now = millis();
  wifiServiceNonBlocking(now);
  mqttServiceNonBlocking(now);
}

unsigned long netLastCmdMillis() { return lastCmdMillis; }

bool netIsConnected() {
  return WiFi.status() == WL_CONNECTED && mqtt.connected();
}
