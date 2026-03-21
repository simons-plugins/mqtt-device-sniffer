#! /usr/bin/env python
# -*- coding: utf-8 -*-

import json
import logging
import queue
import threading
import time

try:
    import paho.mqtt.client as mqtt
    PAHO_AVAILABLE = True
except ImportError:
    PAHO_AVAILABLE = False


class ThreadMqttHandler(threading.Thread):
    """Manages MQTT connection for the sniffer coordinator device."""

    def __init__(self, dev_id, broker_host, broker_port, username, password,
                 root_topic, message_queue, logger=None):
        threading.Thread.__init__(self)
        self.daemon = True

        self.dev_id = dev_id
        self.broker_host = broker_host
        self.broker_port = int(broker_port)
        self.username = username
        self.password = password
        self.root_topic = root_topic
        self.message_queue = message_queue

        self.logger = logger or logging.getLogger("Plugin.MQTT")

        self.mqtt_client = None
        self.connected = False
        self.stop_event = threading.Event()
        self.message_sequence = 0

    def run(self):
        try:
            self.mqtt_client = mqtt.Client(
                client_id=f"indigo-mqtt-sniffer-{self.dev_id}",
                clean_session=True,
                protocol=mqtt.MQTTv311
            )

            self.mqtt_client.on_connect = self._on_connect
            self.mqtt_client.on_disconnect = self._on_disconnect
            self.mqtt_client.on_message = self._on_message

            if self.username:
                self.mqtt_client.username_pw_set(self.username, self.password)

            try:
                self.mqtt_client.connect(
                    host=self.broker_host,
                    port=self.broker_port,
                    keepalive=60
                )
            except Exception as err:
                self.logger.error(
                    f"Unable to connect to MQTT broker at "
                    f"{self.broker_host}:{self.broker_port}: {err}"
                )
                self.message_queue.put({
                    "type": "connection_status",
                    "dev_id": self.dev_id,
                    "status": "disconnected",
                    "error": str(err)
                })
                return

            self.mqtt_client.loop_start()

            while not self.stop_event.is_set():
                time.sleep(1)

            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

        except Exception as err:
            self.logger.error(f"MQTT handler thread error: {err}")

    def stop(self):
        self.stop_event.set()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            subscription = f"{self.root_topic}/#"
            client.subscribe(subscription, qos=1)
            self.logger.info(
                f"Connected to MQTT broker at {self.broker_host}:{self.broker_port}, "
                f"subscribed to {subscription}"
            )
            self.message_queue.put({
                "type": "connection_status",
                "dev_id": self.dev_id,
                "status": "connected"
            })
        else:
            self.logger.error(f"MQTT connection failed with code {rc}")
            self.message_queue.put({
                "type": "connection_status",
                "dev_id": self.dev_id,
                "status": "disconnected",
                "error": f"Connection refused (code {rc})"
            })

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            self.logger.warning(
                f"Unexpected MQTT disconnection (code {rc}), will auto-reconnect"
            )
        self.message_queue.put({
            "type": "connection_status",
            "dev_id": self.dev_id,
            "status": "disconnected"
        })

    def _on_message(self, client, userdata, msg):
        try:
            self.message_sequence += 1
            topic = msg.topic
            payload_str = msg.payload.decode("utf-8")

            try:
                payload = json.loads(payload_str)
            except (json.JSONDecodeError, ValueError):
                payload = payload_str

            topic_parts = topic.split("/")

            self.message_queue.put({
                "type": "mqtt_message",
                "dev_id": self.dev_id,
                "sequence": self.message_sequence,
                "topic": topic,
                "topic_parts": topic_parts,
                "payload": payload,
                "timestamp": time.time()
            })

        except Exception as err:
            self.logger.error(f"Error processing MQTT message: {err}")
