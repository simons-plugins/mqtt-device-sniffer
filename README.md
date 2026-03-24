# MQTT Device Sniffer

An [Indigo](https://www.indigodomo.com) plugin that captures MQTT topics and payloads from any device, builds a structured device profile, and submits it to the [Device Factory](https://github.com/simons-plugins/device-factory) for automated plugin generation.

**Instead of hand-writing an Indigo plugin for every MQTT device, point the sniffer at it for 60 seconds and let the factory build one for you.**

## How It Works

```
┌──────────────┐      MQTT       ┌────────────────────┐     webhook     ┌─────────────────┐
│  Your Device │  ─────────────► │  MQTT Device        │  ────────────► │  Device Factory  │
│  (e.g.       │   publishes     │  Sniffer            │   submits      │                  │
│  Shelly,     │   topics        │                     │   profile      │  Generates an    │
│  Tasmota)    │                 │  Captures topics,   │                │  Indigo plugin   │
└──────────────┘                 │  infers types,      │                │  for your device │
                                 │  anonymises data    │                └─────────────────┘
                                 └────────────────────┘
```

1. **Configure** — Tell the sniffer where your MQTT broker is and which root topic to listen to.
2. **Capture** — The plugin subscribes to `{root}/#` and records every message for a configurable duration (default 60 s).
3. **Review** — Inspect the captured topic tree, field types, and sample payloads in the Indigo event log.
4. **Submit** — Send the anonymised device profile to the Device Factory, which generates a ready-to-install Indigo plugin.

## Installation

1. Download the latest `MQTT-Device-Sniffer.indigoPlugin.zip` from the [Releases](https://github.com/simons-plugins/mqtt-device-sniffer/releases) page.
2. Double-click the `.indigoPlugin` file to install it in Indigo.
3. There are no external dependencies — `paho-mqtt` is bundled with the plugin.

## Setup

Open **Plugins → MQTT Device Sniffer → Configure…** and fill in:

### MQTT Broker

| Setting | Description | Default |
|---------|-------------|---------|
| Broker Host | Hostname or IP of your MQTT broker | `localhost` |
| Broker Port | MQTT port | `1883` |
| Username | Broker username (if authentication is required) | _(empty)_ |
| Password | Broker password (if authentication is required) | _(empty)_ |

### Device to Capture

| Setting | Description | Example |
|---------|-------------|---------|
| Device Name | A friendly name for the device you're capturing | `Shelly Plus 2PM` |
| Manufacturer | The device manufacturer (optional but helpful) | `Shelly` |
| Root Topic | The base MQTT topic for the device | `shelly/plus2pm` |

> **Tip:** Use [MQTT Explorer](https://mqtt-explorer.com) to browse your broker and find the correct root topic for your device before starting a capture.

### Capture Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Capture Duration | How long to listen for messages (seconds) | `60` |

Longer durations capture more topic variations and update patterns. 60 seconds is a good starting point — increase to 120–300 s for devices that publish infrequently.

### Submission

| Setting | Description | Default |
|---------|-------------|---------|
| Webhook Relay URL | Endpoint for device profile submission | `https://factory.domio-smart-home.app/v1/submit` |

The default URL points to the Device Factory. You generally don't need to change this.

## Usage

All actions are under **Plugins → MQTT Device Sniffer** in the Indigo menu.

### Step 1: Capture Device

Select **Capture Device** from the menu. The plugin will:

- Connect to your MQTT broker
- Subscribe to `{rootTopic}/#`
- Collect all messages for the configured duration
- Automatically disconnect when the capture window closes

While capturing, the coordinator device in Indigo shows live counts of topics and messages discovered. You can also watch the event log for progress.

### Step 2: Review Captured Data

Select **Review Captured Data** to see a summary in the event log:

```
═══════════════════════════════════════
  Device: Shelly Plus 2PM (Shelly)
  Root topic: shelly/plus2pm
  Messages: 47 across 12 topics
═══════════════════════════════════════
  shelly/plus2pm/status/switch:0  (8 msgs, on_change)
    output .............. bool
    source .............. string
    temperature.tC ...... float
    temperature.tF ...... float

  shelly/plus2pm/status/switch:1  (6 msgs, on_change)
    output .............. bool
    ...
```

Check that the topics, field types, and message counts look reasonable. If a topic is missing, try a longer capture duration or interact with the device during capture (toggle switches, change settings, etc.) to trigger more messages.

### Step 3: Submit for Plugin Generation

Select **Submit for Plugin Generation**. The plugin will:

- Build the device profile with anonymised sample payloads
- POST it to the Device Factory webhook
- Log a tracking URL so you can follow the plugin generation progress

The submission happens in the background — Indigo won't freeze while it uploads.

### Step 4: Get Your Plugin

The Device Factory processes your device profile and generates a custom Indigo plugin. Check the tracking URL from step 3 for status and download links.

## Privacy and Anonymisation

Before any data is displayed or submitted, the plugin automatically scrubs:

| Data Type | Example | Replaced With |
|-----------|---------|---------------|
| IP addresses | `192.168.1.42` | `x.x.x.x` |
| MAC addresses | `AA:BB:CC:DD:EE:FF` | `XX:XX:XX:XX:XX:XX` |
| Email addresses | `user@home.net` | `redacted@example.com` |
| GPS coordinates | `37.7749` | `0.0000` |

Only the topic structure, field types, and sanitised sample payloads are included in submissions. No raw credentials, network details, or location data leave your system.

## What Gets Submitted

The device profile sent to the Device Factory looks like this:

```json
{
  "deviceName": "Shelly Plus 2PM",
  "manufacturer": "Shelly",
  "rootTopic": "shelly/plus2pm",
  "captureDuration": 60,
  "topicTree": {
    "shelly/plus2pm/status/switch:0": {
      "samplePayloads": ["...anonymised..."],
      "updateFrequency": "on_change",
      "messageCount": 8,
      "fieldTypes": {
        "output": "bool",
        "temperature.tC": "float"
      }
    }
  },
  "topicCount": 12,
  "totalMessages": 47,
  "uniquePayloadCount": 32
}
```

The factory uses this to determine which Indigo device states to create, what types they should be, and how often to poll for updates.

## Troubleshooting

**No messages captured**
- Verify the broker host, port, and credentials are correct
- Confirm the root topic matches your device — use MQTT Explorer to check
- Make sure the device is powered on and publishing during the capture window

**Capture shows fewer topics than expected**
- Increase the capture duration to catch infrequent messages
- Interact with the device during capture (toggle a switch, change a setting) to trigger event-driven topics

**Submission fails**
- Check the Indigo event log for the specific error (network timeout, HTTP error code)
- Verify your Mac has internet access
- The webhook URL should be `https://factory.domio-smart-home.app/v1/submit` unless you've been given a different endpoint

**MQTT connection error**
- Ensure no firewall is blocking the broker port
- If using authentication, double-check the username and password
- The plugin uses MQTT v3.1.1 — confirm your broker supports it

## Coordinator Device

The plugin automatically creates a single **MQTT Sniffer** coordinator device. This device shows capture status at a glance:

| State | Values | Meaning |
|-------|--------|---------|
| Status | `idle` / `capturing` / `captured` / `error` | Current capture state |
| MQTT Status | `connected` / `disconnected` | Broker connection state |
| Captured Topics | Number | Unique topics found |
| Captured Payloads | Number | Total messages received |
| Last Capture | Timestamp | When the last capture completed |

You don't need to create or configure this device — it's managed automatically.

## License

See [LICENSE](LICENSE) for details.
