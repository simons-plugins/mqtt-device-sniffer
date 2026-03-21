#! /usr/bin/env python
# -*- coding: utf-8 -*-

import json
import sys
import time
import unittest
from pathlib import Path

# Add Server Plugin to path so we can import capture
sys.path.insert(0, str(Path(__file__).parent.parent / "MQTT Device Sniffer.indigoPlugin" / "Contents" / "Server Plugin"))

from capture import CaptureSession


class TestTypeOf(unittest.TestCase):
    """Test CaptureSession._type_of value type inference."""

    def setUp(self):
        self.session = CaptureSession("test/#", "Test", "TestCo")

    def test_bool(self):
        self.assertEqual(self.session._type_of(True), "bool")
        self.assertEqual(self.session._type_of(False), "bool")

    def test_bool_before_int(self):
        # Python: isinstance(True, int) is True — bool must be checked first
        self.assertEqual(self.session._type_of(True), "bool")
        self.assertNotEqual(self.session._type_of(True), "int")

    def test_int(self):
        self.assertEqual(self.session._type_of(42), "int")
        self.assertEqual(self.session._type_of(0), "int")
        self.assertEqual(self.session._type_of(-1), "int")

    def test_float(self):
        self.assertEqual(self.session._type_of(3.14), "float")
        self.assertEqual(self.session._type_of(0.0), "float")

    def test_string(self):
        self.assertEqual(self.session._type_of("hello"), "string")
        self.assertEqual(self.session._type_of(""), "string")

    def test_bool_string(self):
        for val in ("true", "false", "on", "off", "yes", "no", "TRUE", "False", "ON"):
            self.assertEqual(self.session._type_of(val), "bool_string", f"Failed for {val}")

    def test_array(self):
        self.assertEqual(self.session._type_of([1, 2, 3]), "array")
        self.assertEqual(self.session._type_of([]), "array")

    def test_object(self):
        self.assertEqual(self.session._type_of({"a": 1}), "object")

    def test_null(self):
        self.assertEqual(self.session._type_of(None), "null")


class TestInferTypes(unittest.TestCase):
    """Test CaptureSession._infer_types recursive field extraction."""

    def setUp(self):
        self.session = CaptureSession("test/#", "Test", "TestCo")

    def test_flat_dict(self):
        field_types = {}
        self.session._infer_types(field_types, {"output": True, "power": 42.5}, prefix="")
        self.assertEqual(field_types, {"output": "bool", "power": "float"})

    def test_nested_dict(self):
        field_types = {}
        self.session._infer_types(field_types, {
            "temperature": {"tC": 22.5, "tF": 72.5}
        }, prefix="")
        self.assertEqual(field_types, {
            "temperature.tC": "float",
            "temperature.tF": "float",
        })

    def test_deeply_nested(self):
        field_types = {}
        self.session._infer_types(field_types, {
            "a": {"b": {"c": 1}}
        }, prefix="")
        self.assertEqual(field_types, {"a.b.c": "int"})

    def test_array_of_objects(self):
        field_types = {}
        self.session._infer_types(field_types, {
            "sensors": [{"id": 1, "temp": 20.0}]
        }, prefix="")
        self.assertEqual(field_types, {
            "sensors[].id": "int",
            "sensors[].temp": "float",
        })

    def test_type_widening(self):
        field_types = {}
        self.session._infer_types(field_types, {"val": 1}, prefix="")
        self.assertEqual(field_types["val"], "int")
        self.session._infer_types(field_types, {"val": "hello"}, prefix="")
        self.assertEqual(field_types["val"], "mixed")

    def test_consistent_type_no_widen(self):
        field_types = {}
        self.session._infer_types(field_types, {"val": 1}, prefix="")
        self.session._infer_types(field_types, {"val": 2}, prefix="")
        self.assertEqual(field_types["val"], "int")


class TestAnonymise(unittest.TestCase):
    """Test CaptureSession._anonymise data scrubbing."""

    def setUp(self):
        self.session = CaptureSession("test/#", "Test", "TestCo")

    def test_ip_address(self):
        result = self.session._anonymise("host is 192.168.1.100 here")
        self.assertEqual(result, "host is x.x.x.x here")

    def test_multiple_ips(self):
        result = self.session._anonymise("10.0.0.1 and 172.16.0.5")
        self.assertEqual(result, "x.x.x.x and x.x.x.x")

    def test_mac_address_colon(self):
        result = self.session._anonymise("mac: AA:BB:CC:DD:EE:FF")
        self.assertEqual(result, "mac: XX:XX:XX:XX:XX:XX")

    def test_mac_address_dash(self):
        result = self.session._anonymise("mac: AA-BB-CC-DD-EE-FF")
        self.assertEqual(result, "mac: XX:XX:XX:XX:XX:XX")

    def test_email(self):
        result = self.session._anonymise("contact user@example.com please")
        self.assertEqual(result, "contact redacted@example.com please")

    def test_gps_coordinate(self):
        result = self.session._anonymise("51.5074")
        self.assertEqual(result, "0.0000")

    def test_non_gps_number(self):
        # Short decimals should NOT be redacted
        result = self.session._anonymise("22.5")
        self.assertEqual(result, "22.5")

    def test_nested_dict(self):
        result = self.session._anonymise({
            "ip": "192.168.1.1",
            "nested": {"mac": "AA:BB:CC:DD:EE:FF"},
        })
        self.assertEqual(result, {
            "ip": "x.x.x.x",
            "nested": {"mac": "XX:XX:XX:XX:XX:XX"},
        })

    def test_list(self):
        result = self.session._anonymise(["192.168.1.1", "hello"])
        self.assertEqual(result, ["x.x.x.x", "hello"])

    def test_non_string_passthrough(self):
        self.assertEqual(self.session._anonymise(42), 42)
        self.assertEqual(self.session._anonymise(True), True)
        self.assertEqual(self.session._anonymise(None), None)

    def test_empty_string(self):
        self.assertEqual(self.session._anonymise(""), "")


class TestUpdateFrequency(unittest.TestCase):
    """Test CaptureSession._update_frequency interval classification."""

    def setUp(self):
        self.session = CaptureSession("test/#", "Test", "TestCo")

    def test_single_message(self):
        self.assertEqual(self.session._update_frequency([1000.0]), "single")

    def test_sub_second(self):
        ts = [1000.0, 1000.5, 1000.9]
        self.assertEqual(self.session._update_frequency(ts), "sub_second")

    def test_frequent(self):
        ts = [1000.0, 1003.0, 1006.0]
        self.assertEqual(self.session._update_frequency(ts), "frequent")

    def test_periodic(self):
        ts = [1000.0, 1015.0, 1030.0]
        self.assertEqual(self.session._update_frequency(ts), "periodic")

    def test_slow(self):
        ts = [1000.0, 1060.0, 1120.0]
        self.assertEqual(self.session._update_frequency(ts), "slow")

    def test_on_change(self):
        ts = [1000.0, 1300.0]
        self.assertEqual(self.session._update_frequency(ts), "on_change")


class TestBuildProfile(unittest.TestCase):
    """Test CaptureSession.build_profile end-to-end."""

    def setUp(self):
        self.session = CaptureSession("shelly/pm", "Shelly PM", "Shelly", duration=5)

    def test_empty_session(self):
        self.assertIsNone(self.session.build_profile())

    def test_full_session(self):
        self.session.start()
        now = time.time()

        self.session.add_message("shelly/pm/status/switch:0", {
            "output": True, "source": "button",
            "temperature": {"tC": 42.3, "tF": 108.2}
        }, now)

        self.session.add_message("shelly/pm/status/switch:0", {
            "output": False, "source": "mqtt",
            "temperature": {"tC": 41.0, "tF": 105.8}
        }, now + 2)

        self.session.add_message("shelly/pm/status/power", 150.5, now + 1)

        profile = self.session.build_profile()

        self.assertEqual(profile["deviceName"], "Shelly PM")
        self.assertEqual(profile["manufacturer"], "Shelly")
        self.assertEqual(profile["rootTopic"], "shelly/pm")
        self.assertEqual(profile["topicCount"], 2)
        self.assertEqual(profile["totalMessages"], 3)
        self.assertIn("shelly/pm/status/switch:0", profile["topicTree"])
        self.assertIn("shelly/pm/status/power", profile["topicTree"])

        switch_entry = profile["topicTree"]["shelly/pm/status/switch:0"]
        self.assertEqual(switch_entry["messageCount"], 2)
        self.assertIn("output", switch_entry["fieldTypes"])
        self.assertEqual(switch_entry["fieldTypes"]["output"], "bool")
        self.assertEqual(switch_entry["fieldTypes"]["temperature.tC"], "float")

    def test_profile_anonymises_payloads(self):
        self.session.start()
        now = time.time()

        self.session.add_message("shelly/pm/info", {
            "ip": "192.168.1.50",
            "mac": "AA:BB:CC:DD:EE:FF",
        }, now)

        profile = self.session.build_profile()
        sample = profile["topicTree"]["shelly/pm/info"]["samplePayloads"][0]
        self.assertEqual(sample["ip"], "x.x.x.x")
        self.assertEqual(sample["mac"], "XX:XX:XX:XX:XX:XX")

    def test_unique_payload_dedup(self):
        self.session.start()
        now = time.time()

        # Same payload twice → only 1 unique
        for i in range(3):
            self.session.add_message("shelly/pm/status", {"val": 1}, now + i)

        profile = self.session.build_profile()
        self.assertEqual(len(profile["topicTree"]["shelly/pm/status"]["samplePayloads"]), 1)
        self.assertEqual(profile["uniquePayloadCount"], 1)

    def test_max_sample_payloads(self):
        self.session.start()
        now = time.time()

        # 10 different payloads → capped at 5 samples
        for i in range(10):
            self.session.add_message("shelly/pm/data", {"val": i}, now + i)

        profile = self.session.build_profile()
        self.assertEqual(len(profile["topicTree"]["shelly/pm/data"]["samplePayloads"]), 5)


class TestCaptureSessionLifecycle(unittest.TestCase):
    """Test capture session state management."""

    def test_not_active_before_start(self):
        session = CaptureSession("test/#", "Test", "TestCo", duration=60)
        self.assertFalse(session.is_active)

    def test_active_after_start(self):
        session = CaptureSession("test/#", "Test", "TestCo", duration=60)
        session.start()
        self.assertTrue(session.is_active)

    def test_inactive_after_duration(self):
        session = CaptureSession("test/#", "Test", "TestCo", duration=0)
        session.start()
        time.sleep(0.05)
        self.assertFalse(session.is_active)

    def test_add_message_rejected_when_inactive(self):
        session = CaptureSession("test/#", "Test", "TestCo", duration=0)
        time.sleep(0.05)
        result = session.add_message("test/topic", {"a": 1}, time.time())
        self.assertFalse(result)
        self.assertEqual(len(session.messages), 0)

    def test_string_payload(self):
        session = CaptureSession("test/#", "Test", "TestCo", duration=60)
        session.start()
        result = session.add_message("test/topic", "just a string", time.time())
        self.assertTrue(result)
        self.assertEqual(len(session.messages), 1)
        # No field types inferred for non-dict payloads
        self.assertEqual(session.topic_data["test/topic"]["field_types"], {})

    def test_summary_lines(self):
        session = CaptureSession("test/#", "Test", "TestCo", duration=60)
        session.start()
        session.add_message("test/status", {"online": True}, time.time())
        session.stop()
        lines = session.get_summary_lines()
        self.assertTrue(any("Test" in line for line in lines))
        self.assertTrue(any("test/status" in line for line in lines))

    def test_summary_no_data(self):
        session = CaptureSession("test/#", "Test", "TestCo")
        lines = session.get_summary_lines()
        self.assertEqual(lines, ["No data captured yet."])


if __name__ == "__main__":
    unittest.main()
