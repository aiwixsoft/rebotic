"""MQTT publisher — the only line to the ESP32 body.

Sends intent, never servo angles. Fail silent everywhere: the robot body is
optional, the conversation is not. If the broker is down, every publish is a
quiet no-op and the voice pipeline keeps running.
"""

import json

import paho.mqtt.client as mqtt

import config


class Robot:
    def __init__(
        self,
        host: str = config.MQTT_HOST,
        port: int = config.MQTT_PORT,
        topic: str = config.MQTT_TOPIC,
        qos: int = config.MQTT_QOS,
    ) -> None:
        self._topic = topic
        self._qos = qos
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        # connect_async + loop_start: never blocks, never raises, auto-reconnects
        self._client.connect_async(host, port)
        self._client.loop_start()

    def _publish(self, payload: dict) -> None:
        try:
            self._client.publish(self._topic, json.dumps(payload), qos=self._qos)
        except Exception:
            pass  # body offline — conversation continues

    def state(self, v: str) -> None:
        """idle | attend | thinking | speak"""
        self._publish({"t": "state", "v": v})

    def amp(self, v: int) -> None:
        """Mouth amplitude 0-255, 40Hz during speech. Always send 0 on stop."""
        self._publish({"t": "amp", "v": v})

    def gest(self, v: str) -> None:
        """nod | tilt | shrug"""
        self._publish({"t": "gest", "v": v})

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


if __name__ == "__main__":
    # Smoke test: watch with `mosquitto_sub -t rebotic/cmd -v`
    import time

    robot = Robot()
    time.sleep(0.5)  # let the async connect land
    robot.state("attend")
    robot.gest("nod")
    for v in (0, 80, 200, 120, 0):
        robot.amp(v)
        time.sleep(0.1)
    robot.state("idle")
    time.sleep(0.5)
    robot.close()
    print("sent: state/gest/amp on rebotic/cmd (silent no-op if broker is down)")
