# CLAUDE.md

## Plugin Overview

**MQTT Device Sniffer** — Indigo plugin that captures MQTT topics and payloads from any device, builds a structured device profile, and submits it for automated plugin generation.

- **Version**: 2026.0.0
- **Bundle ID**: `com.simons-plugins.mqtt-device-sniffer`
- **Transport**: MQTT via paho-mqtt 2.x (bundled)
- **GitHub**: https://github.com/simons-plugins/mqtt-device-sniffer

## Architecture

### Single Coordinator

One `snifferCoordinator` device (auto-created) manages temporary MQTT connections for capture sessions. No child devices — the sniffer captures data, it doesn't manage devices.

### Capture Flow

1. User configures broker + root topic + device name in plugin config
2. User clicks "Capture Device" menu item
3. Plugin connects to MQTT, subscribes to `{root}/#`
4. Captures all messages for configured duration (default 60s)
5. Disconnects from MQTT
6. User reviews captured data via "Review Captured Data"
7. User submits via "Submit for Plugin Generation" → webhook relay

### Message Flow

MQTT broker → paho callback → queue.Queue → plugin.py drains → CaptureSession collects → builds profile → anonymises → POST to webhook

### Modules

| Module | Purpose |
|--------|---------|
| `mqtt_handler.py` | ThreadMqttHandler — temporary MQTT connection |
| `capture.py` | CaptureSession — message collection, type inference, anonymisation, profile building |
| `plugin.py` | Plugin lifecycle, menu items, submission |

### Anonymisation

Before display or submission, payloads are scrubbed of:
- IP addresses → `x.x.x.x`
- MAC addresses → `XX:XX:XX:XX:XX:XX`
- Email-like strings → `redacted@example.com`
- GPS coordinates (high-precision decimals) → `0.0000`

### Device Profile Format

```json
{
  "deviceName": "Shelly Plus 2PM",
  "manufacturer": "Shelly",
  "rootTopic": "shelly/plus2pm",
  "capturedAt": "2026-03-21T15:00:00Z",
  "captureDuration": 60,
  "topicTree": {
    "shelly/plus2pm/status/switch:0": {
      "samplePayloads": [...],
      "updateFrequency": "on_change",
      "messageCount": 3,
      "fieldTypes": {"output": "bool", "temperature.tC": "float"}
    }
  },
  "topicCount": 12,
  "uniquePayloadCount": 8
}
```

## Menu Items

| Menu Item | Purpose |
|-----------|---------|
| Capture Device | Start timed MQTT capture session |
| Review Captured Data | Show captured profile in event log |
| Submit for Plugin Generation | POST profile to webhook relay |
| Report Plugin Issue | Report issues with generated plugins |

## Testing

```bash
cp -r "MQTT Device Sniffer.indigoPlugin" "/Volumes/Macintosh HD-1/Library/Application Support/Perceptive Automation/Indigo 2025.1/Plugins/"
```

## Release Process

- Bump `PluginVersion` in Info.plist with every PR — CI enforces this
- Version format: `YYYY.R.P` (e.g. `2026.0.0`)
- On merge to main, auto-creates tagged GitHub release with `.indigoPlugin.zip`

## Dependencies (bundled)

- `paho-mqtt` 2.1.0 — MQTT client
