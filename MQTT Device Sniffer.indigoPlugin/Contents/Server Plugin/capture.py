#! /usr/bin/env python
# -*- coding: utf-8 -*-

import json
import logging
import re
import time


# Patterns to anonymise from payload values
_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_MAC_PATTERN = re.compile(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}")
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_GPS_PATTERN = re.compile(r"-?\d{1,3}\.\d{4,}")


class CaptureSession:
    """Captures MQTT messages and builds a structured device profile."""

    def __init__(self, root_topic, device_name, manufacturer, duration=60, logger=None):
        self.root_topic = root_topic
        self.device_name = device_name
        self.manufacturer = manufacturer
        self.duration = duration
        self.logger = logger or logging.getLogger("Plugin.Capture")

        self.start_time = None
        self.end_time = None
        self.messages = []  # raw captured messages
        self.topic_data = {}  # topic -> {payloads, timestamps, field_types}

    @property
    def is_active(self):
        if self.start_time is None:
            return False
        return time.time() < self.start_time + self.duration

    @property
    def elapsed(self):
        if self.start_time is None:
            return 0
        return min(time.time() - self.start_time, self.duration)

    @property
    def remaining(self):
        if self.start_time is None:
            return self.duration
        return max(0, self.duration - (time.time() - self.start_time))

    def start(self):
        self.start_time = time.time()
        self.end_time = None
        self.messages = []
        self.topic_data = {}
        self.logger.info(f"Capture started for {self.duration}s on {self.root_topic}/#")

    def stop(self):
        self.end_time = time.time()
        self.logger.info(
            f"Capture complete: {len(self.topic_data)} topics, "
            f"{len(self.messages)} messages in {self.elapsed:.0f}s"
        )

    def add_message(self, topic, payload, timestamp):
        """Record an MQTT message during capture."""
        if not self.is_active:
            self.logger.debug(f"Message rejected (capture not active): {topic}")
            return False

        self.messages.append({
            "topic": topic,
            "payload": payload,
            "timestamp": timestamp
        })

        if topic not in self.topic_data:
            self.topic_data[topic] = {
                "sample_payloads": [],
                "timestamps": [],
                "field_types": {},
            }

        td = self.topic_data[topic]
        td["timestamps"].append(timestamp)

        # Keep up to 5 unique sample payloads per topic
        payload_str = json.dumps(payload, sort_keys=True) if isinstance(payload, (dict, list)) else str(payload)
        existing = [json.dumps(p, sort_keys=True) if isinstance(p, (dict, list)) else str(p) for p in td["sample_payloads"]]
        if payload_str not in existing and len(td["sample_payloads"]) < 5:
            td["sample_payloads"].append(payload)

        # Infer field types
        if isinstance(payload, dict):
            self._infer_types(td["field_types"], payload, prefix="")

        return True

    def _infer_types(self, field_types, obj, prefix):
        """Recursively infer field types from a dict payload."""
        for key, value in obj.items():
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            inferred = self._type_of(value)

            if isinstance(value, dict):
                self._infer_types(field_types, value, full_key)
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                # Array of objects — infer from first element
                self._infer_types(field_types, value[0], f"{full_key}[]")
            else:
                if full_key in field_types:
                    # Widen type if we see different types for the same field
                    if field_types[full_key] != inferred:
                        field_types[full_key] = "mixed"
                else:
                    field_types[full_key] = inferred

    def _type_of(self, value):
        """Infer the data type of a value."""
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            low = value.lower()
            if low in ("true", "false", "on", "off", "yes", "no"):
                return "bool_string"
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        if value is None:
            return "null"
        return "unknown"

    def _update_frequency(self, timestamps):
        """Determine update frequency from timestamps."""
        if len(timestamps) < 2:
            return "single"

        intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps) - 1)]
        avg_interval = sum(intervals) / len(intervals)

        if avg_interval < 1:
            return "sub_second"
        if avg_interval < 5:
            return "frequent"  # every few seconds
        if avg_interval < 30:
            return "periodic"  # every 5-30s
        if avg_interval < 120:
            return "slow"  # every 30s-2min
        return "on_change"  # infrequent, likely event-driven

    def build_profile(self):
        """Build the structured device profile for submission."""
        if not self.topic_data:
            return None

        topic_tree = {}
        for topic, data in sorted(self.topic_data.items()):
            anonymised_payloads = [self._anonymise(p) for p in data["sample_payloads"]]
            topic_tree[topic] = {
                "samplePayloads": anonymised_payloads,
                "updateFrequency": self._update_frequency(data["timestamps"]),
                "messageCount": len(data["timestamps"]),
                "fieldTypes": data["field_types"],
            }

        unique_payloads = set()
        for data in self.topic_data.values():
            for p in data["sample_payloads"]:
                key = json.dumps(p, sort_keys=True) if isinstance(p, (dict, list)) else str(p)
                unique_payloads.add(key)

        return {
            "deviceName": self.device_name,
            "manufacturer": self.manufacturer,
            "rootTopic": self.root_topic,
            "captureDuration": self.duration,
            "actualDuration": round(self.elapsed, 1),
            "topicTree": topic_tree,
            "topicCount": len(self.topic_data),
            "totalMessages": len(self.messages),
            "uniquePayloadCount": len(unique_payloads),
        }

    def _anonymise(self, value):
        """Recursively anonymise sensitive data from a payload."""
        if isinstance(value, dict):
            return {k: self._anonymise(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._anonymise(item) for item in value]
        if isinstance(value, str):
            result = value
            result = _IP_PATTERN.sub("x.x.x.x", result)
            result = _MAC_PATTERN.sub("XX:XX:XX:XX:XX:XX", result)
            result = _EMAIL_PATTERN.sub("redacted@example.com", result)
            # GPS coords: only redact if the value itself looks like a standalone coordinate
            if _GPS_PATTERN.fullmatch(result.strip()):
                result = "0.0000"
            return result
        return value

    def get_summary_lines(self):
        """Return human-readable summary lines for the event log."""
        if not self.topic_data:
            return ["No data captured yet."]

        lines = [
            f"Device: {self.device_name} ({self.manufacturer})",
            f"Root Topic: {self.root_topic}",
            f"Captured: {len(self.messages)} messages across {len(self.topic_data)} topics",
            f"Duration: {self.elapsed:.0f}s",
            "",
            "Topic Tree:",
        ]

        for topic, data in sorted(self.topic_data.items()):
            freq = self._update_frequency(data["timestamps"])
            msg_count = len(data["timestamps"])
            lines.append(f"  {topic}  ({msg_count} msgs, {freq})")

            if data["field_types"]:
                for field, ftype in sorted(data["field_types"].items()):
                    lines.append(f"    {field}: {ftype}")

            if data["sample_payloads"]:
                sample = data["sample_payloads"][0]
                if isinstance(sample, dict):
                    sample_str = json.dumps(self._anonymise(sample), indent=None)
                else:
                    sample_str = str(self._anonymise(sample))
                if len(sample_str) > 120:
                    sample_str = sample_str[:117] + "..."
                lines.append(f"    Sample: {sample_str}")

        return lines
