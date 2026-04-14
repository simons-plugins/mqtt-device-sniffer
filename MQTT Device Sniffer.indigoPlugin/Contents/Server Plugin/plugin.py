#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################
# MQTT Device Sniffer - Indigo Plugin
# Captures MQTT topics/payloads and submits device profiles
# for automated plugin generation.
####################

import json
import queue
import time
import threading
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import indigo

try:
    from mqtt_handler import ThreadMqttHandler, PAHO_AVAILABLE
except ImportError:
    PAHO_AVAILABLE = False

from capture import CaptureSession


class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs, **kwargs):
        super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs, **kwargs)
        self.debug = pluginPrefs.get("showDebugInfo", False)

        self.coordinator_id = None
        self.mqtt_thread = None
        self.msg_queue = queue.Queue()
        self.capture_session = None

    # -------------------------------------------------------------------------
    # Plugin lifecycle
    # -------------------------------------------------------------------------

    def startup(self):
        self.logger.info("MQTT Device Sniffer starting")
        if not PAHO_AVAILABLE:
            self.logger.error(
                "paho-mqtt library not found. Ensure it is in the plugin's Packages/ directory."
            )

        # Auto-create coordinator device if none exists
        coordinator_exists = False
        for dev in indigo.devices.iter("self.snifferCoordinator"):
            coordinator_exists = True
            break

        if not coordinator_exists:
            self._create_coordinator_device()

    def shutdown(self):
        self.logger.info("MQTT Device Sniffer stopping")
        self._stop_mqtt()

    def runConcurrentThread(self):
        try:
            while True:
                self._drain_queue()

                # Check if capture has finished
                if self.capture_session and self.capture_session.start_time and not self.capture_session.is_active:
                    if self.capture_session.end_time is None:
                        self._finish_capture()

                self.sleep(0.1)
        except self.StopThread:
            pass

    # -------------------------------------------------------------------------
    # Device lifecycle
    # -------------------------------------------------------------------------

    def deviceStartComm(self, dev):
        if dev.deviceTypeId == "snifferCoordinator":
            self.coordinator_id = dev.id
            dev.updateStateOnServer("status", "idle")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)
            self.logger.debug(f"Coordinator device started: {dev.name}")

    def deviceStopComm(self, dev):
        if dev.deviceTypeId == "snifferCoordinator":
            self._stop_mqtt()
            self.coordinator_id = None

    # -------------------------------------------------------------------------
    # Preferences
    # -------------------------------------------------------------------------

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self.debug = valuesDict.get("showDebugInfo", False)

    # -------------------------------------------------------------------------
    # Auto-create coordinator device
    # -------------------------------------------------------------------------

    def _create_coordinator_device(self):
        try:
            dev = indigo.device.create(
                protocol=indigo.kProtocol.Plugin,
                deviceTypeId="snifferCoordinator",
                name="MQTT Device Sniffer",
            )
            dev.updateStateOnServer("status", "idle")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)
            self.logger.info(f"Auto-created sniffer device: {dev.name}")
        except Exception as err:
            self.logger.error(f"Error creating coordinator device: {err}")

    # -------------------------------------------------------------------------
    # MQTT management
    # -------------------------------------------------------------------------

    def _start_mqtt(self):
        if not PAHO_AVAILABLE:
            self.logger.error("Cannot start MQTT — paho-mqtt not available")
            return False

        self._stop_mqtt()

        prefs = self.pluginPrefs
        root_topic = prefs.get("rootTopic", "")
        if not root_topic:
            self.logger.error("No root topic configured. Set it in plugin config.")
            return False

        self.msg_queue = queue.Queue()
        self.mqtt_thread = ThreadMqttHandler(
            dev_id=self.coordinator_id or 0,
            broker_host=prefs.get("brokerHost", "localhost"),
            broker_port=prefs.get("brokerPort", 1883),
            username=prefs.get("mqttUsername", ""),
            password=prefs.get("mqttPassword", ""),
            root_topic=root_topic,
            message_queue=self.msg_queue,
            logger=self.logger
        )
        self.mqtt_thread.start()
        return True

    def _stop_mqtt(self):
        if self.mqtt_thread and self.mqtt_thread.is_alive():
            self.mqtt_thread.stop()
            self.mqtt_thread.join(timeout=5)
            self.logger.debug("MQTT handler stopped")
        self.mqtt_thread = None

    # -------------------------------------------------------------------------
    # Queue processing
    # -------------------------------------------------------------------------

    def _drain_queue(self):
        while not self.msg_queue.empty():
            try:
                msg = self.msg_queue.get_nowait()
            except queue.Empty:
                break

            msg_type = msg.get("type")

            if msg_type == "connection_status":
                self._handle_connection_status(msg)
            elif msg_type == "mqtt_message":
                self._handle_mqtt_message(msg)

    def _handle_connection_status(self, msg):
        status = msg["status"]
        if self.coordinator_id:
            try:
                dev = indigo.devices[self.coordinator_id]
                dev.updateStateOnServer("mqttStatus", status)
                if status == "connected":
                    dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
                    dev.updateStateOnServer("status", "capturing")
                else:
                    if msg.get("error"):
                        dev.updateStateOnServer("status", "error")
                        dev.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                    else:
                        dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)
            except KeyError:
                pass

    def _handle_mqtt_message(self, msg):
        if self.capture_session and self.capture_session.is_active:
            self.capture_session.add_message(
                msg["topic"], msg["payload"], msg["timestamp"]
            )

            # Periodic progress update
            if self.coordinator_id and msg["sequence"] % 10 == 0:
                try:
                    dev = indigo.devices[self.coordinator_id]
                    dev.updateStatesOnServer([
                        {"key": "capturedTopics", "value": len(self.capture_session.topic_data)},
                        {"key": "capturedPayloads", "value": len(self.capture_session.messages)},
                    ])
                except KeyError:
                    pass

    # -------------------------------------------------------------------------
    # Capture lifecycle
    # -------------------------------------------------------------------------

    def _finish_capture(self):
        """Called when the capture duration expires."""
        mqtt_was_connected = self.mqtt_thread and self.mqtt_thread.connected
        self.capture_session.stop()
        self._stop_mqtt()

        if not mqtt_was_connected:
            self.logger.warning(
                "MQTT was disconnected when capture ended — data may be incomplete. "
                "Check broker settings and try again."
            )

        if self.coordinator_id:
            try:
                dev = indigo.devices[self.coordinator_id]
                dev.updateStatesOnServer([
                    {"key": "status", "value": "captured"},
                    {"key": "mqttStatus", "value": "disconnected"},
                    {"key": "capturedTopics", "value": len(self.capture_session.topic_data)},
                    {"key": "capturedPayloads", "value": len(self.capture_session.messages)},
                    {"key": "lastCapture", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                ])
                dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
            except KeyError:
                pass

        self.logger.info(
            f"Capture complete. Use 'Review Captured Data' to inspect, "
            f"then 'Submit for Plugin Generation' to create a plugin."
        )

    # -------------------------------------------------------------------------
    # Menu items
    # -------------------------------------------------------------------------

    def captureDevice(self):
        """Start a timed MQTT capture session."""
        prefs = self.pluginPrefs
        root_topic = prefs.get("rootTopic", "")
        device_name = prefs.get("deviceName", "")
        manufacturer = prefs.get("deviceManufacturer", "")
        duration = int(prefs.get("captureDuration", 60))

        if not root_topic:
            self.logger.error("No root topic configured. Go to Plugins → MQTT Device Sniffer → Configure.")
            return

        if not device_name:
            self.logger.error("No device name configured. Go to Plugins → MQTT Device Sniffer → Configure.")
            return

        if self.capture_session and self.capture_session.is_active:
            self.logger.warning(
                f"Capture already in progress ({self.capture_session.remaining:.0f}s remaining). "
                f"Wait for it to finish."
            )
            return

        self.capture_session = CaptureSession(
            root_topic=root_topic,
            device_name=device_name,
            manufacturer=manufacturer,
            duration=duration,
            logger=self.logger,
        )
        self.capture_session.start()

        if not self._start_mqtt():
            self.capture_session = None
            return

        self.logger.info(
            f"Capturing MQTT data for '{device_name}' on {root_topic}/# for {duration}s..."
        )

    def reviewCapture(self):
        """Show captured data in the Indigo event log."""
        if not self.capture_session or not self.capture_session.topic_data:
            self.logger.info("No captured data. Run 'Capture Device' first.")
            return

        if self.capture_session.is_active:
            self.logger.info(
                f"Capture in progress ({self.capture_session.remaining:.0f}s remaining). "
                f"Wait for completion before reviewing."
            )
            return

        self.logger.info("=" * 60)
        self.logger.info("MQTT Device Sniffer — Captured Data")
        self.logger.info("=" * 60)
        for line in self.capture_session.get_summary_lines():
            self.logger.info(line)
        self.logger.info("=" * 60)
        self.logger.info(
            "If this looks correct, use 'Submit for Plugin Generation' to "
            "request an auto-generated Indigo plugin for this device."
        )

    def submitCapture(self):
        """Post the captured device profile to the webhook relay."""
        if not self.capture_session or not self.capture_session.topic_data:
            self.logger.info("No captured data. Run 'Capture Device' first.")
            return

        if self.capture_session.is_active:
            self.logger.info("Capture still in progress. Wait for completion.")
            return

        profile = self.capture_session.build_profile()
        if not profile:
            self.logger.error("Failed to build device profile.")
            return

        # Add metadata
        profile["capturedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            profile["indigoVersion"] = str(indigo.server.version)
        except Exception:
            profile["indigoVersion"] = "unknown"
        profile["snifferVersion"] = self.pluginVersion

        webhook_url = self.pluginPrefs.get("webhookUrl", "")
        if not webhook_url:
            self.logger.error("No webhook URL configured.")
            return

        self.logger.info(f"Submitting device profile for '{profile['deviceName']}'...")

        # POST in background thread to avoid blocking
        submit_thread = threading.Thread(
            target=self._do_submit,
            args=(webhook_url, profile),
            daemon=True
        )
        submit_thread.start()

    def _relay_auth_header(self):
        """Return the X-Relay-Secret header value from plugin prefs, or empty string."""
        return (self.pluginPrefs.get("relaySharedSecret", "") or "").strip()

    def _do_submit(self, url, profile):
        """Background thread: POST device profile to webhook relay."""
        try:
            data = json.dumps(profile).encode("utf-8")
            req = Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", f"MQTT-Device-Sniffer/{self.pluginVersion}")
            secret = self._relay_auth_header()
            if secret:
                req.add_header("X-Relay-Secret", secret)

            with urlopen(req, timeout=30) as response:
                status = response.status
                body = json.loads(response.read().decode("utf-8"))

            if status in (200, 201):
                issue_url = body.get("issueUrl", "")
                self.logger.info(
                    f"Device profile submitted successfully! "
                    f"Track progress: {issue_url}"
                )
            else:
                self.logger.error(f"Submission failed (HTTP {status}): {body}")

        except HTTPError as err:
            try:
                error_body = err.read().decode("utf-8")
            except Exception:
                error_body = str(err)
            self.logger.error(f"Submission failed (HTTP {err.code}): {error_body}")
        except URLError as err:
            self.logger.error(f"Could not reach webhook relay: {err.reason}")
        except Exception as err:
            self.logger.error(f"Submission error: {err}")

    def reportIssue(self, valuesDict, typeId=""):
        """Post an error report to the relay, which files a GitHub issue on the plugin repo."""
        errors = indigo.Dict()

        plugin_repo = (valuesDict.get("pluginRepo") or "").strip()
        if "/" not in plugin_repo:
            errors["pluginRepo"] = "Expected owner/name format, e.g. simons-plugins/indigo-shelly-plus2pm"

        error_log = (valuesDict.get("errorLog") or "").strip()
        if not error_log:
            errors["errorLog"] = "Paste the relevant error log lines before submitting."

        webhook_url = self.pluginPrefs.get("webhookUrl", "")
        if not webhook_url:
            errors["errorLog"] = "No webhook URL configured in plugin preferences."

        if errors:
            return (False, valuesDict, errors)

        report_url = webhook_url.rsplit("/", 1)[0] + "/report"

        try:
            indigo_version = str(indigo.server.version)
        except Exception:
            indigo_version = "unknown"

        report = {
            "pluginRepo": plugin_repo,
            "pluginVersion": (valuesDict.get("pluginVersion") or "").strip() or "unknown",
            "description": (valuesDict.get("description") or "").strip(),
            "errorLog": error_log,
            "indigoVersion": indigo_version,
        }

        self.logger.info(f"Submitting error report for {plugin_repo}...")
        threading.Thread(
            target=self._do_report,
            args=(report_url, report),
            daemon=True,
        ).start()

        return (True, valuesDict)

    def _do_report(self, url, report):
        """Background thread: POST error report to webhook relay."""
        try:
            data = json.dumps(report).encode("utf-8")
            req = Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", f"MQTT-Device-Sniffer/{self.pluginVersion}")
            secret = self._relay_auth_header()
            if secret:
                req.add_header("X-Relay-Secret", secret)

            with urlopen(req, timeout=30) as response:
                status = response.status
                body = json.loads(response.read().decode("utf-8"))

            if status in (200, 201):
                issue_url = body.get("issueUrl", "")
                self.logger.info(f"Error report filed: {issue_url}")
            else:
                self.logger.error(f"Report failed (HTTP {status}): {body}")

        except HTTPError as err:
            try:
                error_body = err.read().decode("utf-8")
            except Exception:
                error_body = str(err)
            self.logger.error(f"Report failed (HTTP {err.code}): {error_body}")
        except URLError as err:
            self.logger.error(f"Could not reach webhook relay: {err.reason}")
        except Exception as err:
            self.logger.error(f"Report error: {err}")
