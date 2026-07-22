# Spec 04 — BRING-UP (laptop ↔ body integration)

**Scope:** The ordered path from parts-in-a-bag to the full loop running
through the body. Each stage has a pass gate — do not advance on a failing
gate; every later symptom becomes ambiguous if you do.

---

## Stage 0 — Body audio (no ESP32 needed)

Mount and plug in the USB mic + speaker ([01-audio.md](01-audio.md)), then
point the OS defaults at them **before** any Python process starts:

```bash
pactl list short sources && pactl list short sinks   # find the body devices
pactl set-default-source <body-mic>
pactl set-default-sink   <body-speaker>

python voice.py "Hello from my own chest"   # sound comes from the BODY
python hearing.py                            # speak AT THE ROBOT → final transcript
```

**Gate:** both existing standalone tests pass through the body's hardware,
and a `main.py` run at demo volume does **not** barge itself in (echo check).
Zero code was touched — if you edited a `.py` file to pass this stage,
something is wrong with the routing, not the code.

---

## Stage 1 — Broker reachable from the LAN (the classic blocker)

Mosquitto ≥2.0 binds **localhost only** by default. The Python side never
notices (it connects to localhost) — the ESP32 silently can't connect.

```bash
# /etc/mosquitto/conf.d/rebotic.conf
listener 1883 0.0.0.0
allow_anonymous true

sudo systemctl restart mosquitto
```

Network = **laptop's phone-hotspot** (venue WiFi is hostile). The ESP32
firmware gets the hotspot SSID/pass and the laptop's hotspot IP as its MQTT
host — the laptop keeps using `localhost`, so `config.py` is untouched.

**Gate:** from a phone/second device on the hotspot:
`mosquitto_sub -h <laptop-ip> -t rebotic/cmd -v` sees `python robot.py` output.

---

## Stage 2 — Bench electronics (no firmware logic yet)

Order: eyes → servos → button. One subsystem per sketch.

1. **Eyes:** NeoPixel strandtest on GPIO 25. All 16 px light, no flicker.
2. **Servos:** sweep sketch, one servo at a time (jaw, neck pan, head tilt,
   both arms), powered from the external rail. Watch the ESP32 doesn't reset
   during sweeps (if it does: grounds or supply —
   [03-electronics.md](03-electronics.md)).
3. **Button:** serial-print on edge, confirm 1 edge per press (debounce).

**Gate:** all three pass with all five servos attached and sweeping
simultaneously — that's the brownout test.

---

## Stage 3 — Firmware ↔ broker ([02-firmware.md](02-firmware.md) standalone test)

Flash the real firmware. Run the `mosquitto_pub` sequence and the
`python robot.py` smoke test from spec 02. Check every "passes when" item,
**including** the kill-the-broker recovery test.

---

## Stage 4 — Full loop

```bash
python main.py --latency
```

- Greeting plays **from the chest** → jaw moves in sync from the first word
  (greeting is sent before the mic opens — a clean jaw-sync observation
  window, and with the speaker in the body, jaw and sound now share a source).
- Speak *to the robot*: head turns on `speech_start` (attend), eyes pulse
  during thinking, jaw syncs during reply, returns to idle sway.
- A reply with a gesture tag → gesture plays mid-speech without jaw stutter.
- Barge-in via PTT → speech cuts, jaw closes, head re-attends.

**Gate:** `--latency` still reports speech_end→first_audio < 1000ms with the
body connected — body motion must cost ~0ms (fire-and-forget QoS 0) and USB
audio ~10–20ms. If latency regressed, find what's blocking; don't ship it.

---

## Stage 5 — Hardware PTT (needs the laptop-side glue from spec 02)

Once `rebotic/evt` subscription is wired into the laptop (future, explicit
request only):

- Hold button → speak → release → reply. Full turn with **zero keyboard**.
- Button during bot speech → barge-in.

**Gate:** 10 consecutive button-driven turns, no missed press, no double-fire.

---

## Stage 6 — Soak (do this before the expo, not at it)

- 1 hour: `main.py` + body, occasional turns. Servos detach between turns
  (touch-test: cool), RAM flat on the laptop, no firmware reboot (add a
  boot-counter print to detect silent WDT resets), no USB audio dropouts.
- Pull WiFi mid-conversation → body idles gracefully → restore → body
  rejoins alone. Same for restarting `main.py`.
- Unplug/replug the USB mic → confirm defaults survived (re-run the two
  `pactl` commands if the device re-enumerated under a new name).

---

## Expo-day order of operations

```
1. Laptop on mains, on stand           6. Flash-check: python robot.py smoke
2. Body audio plugged, pactl defaults  7. python main.py --latency, one test turn
3. Phone hotspot up, broker running       (spoken AT the robot, reply FROM it)
4. Body powered (servo rail first)     8. PTT test turn
5. Body LED shows MQTT connected       9. Watchdog script running → doors open
```

Degradation paths, worst-first:
- **ESP32 dies** → unplug it. Conversation keeps running (robot.py fails silent).
- **Body mic drowns in hall noise** → `pactl set-default-source <headset>`,
  clip the headset mic to the robot's chest. Still zero code changes.
- **Body speaker dies** → `pactl set-default-sink` back to laptop/any speaker.
