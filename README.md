# TR7 Exalus Home Assistant Integration

A custom Home Assistant integration for controlling TR7 Exalus smart blinds via WebSocket API.

## Features

- Real-time blind position control
- WebSocket-based communication for instant updates
- Support for multiple blinds
- Open, close, stop, and set position commands
- Integration with Home Assistant's cover entity

## Installation

### HACS (Recommended)

1. In Home Assistant, go to HACS → Integrations → ⋮ (overflow menu) → Custom repositories.
2. Paste the URL of this repository (for example, https://github.com/krystaDev/home-assistant-tr7-exalus) and select Category: Integration, then click Add.
3. In HACS → Integrations, search for "TR7 Exalus Blind Control" and click Install.
4. Restart Home Assistant.

Notes:
- Requires Home Assistant 2024.8.0 or newer (set in hacs.json).
- After installation, configure the integration from Settings → Devices & Services → Add Integration → "TR7 Exalus".

### Manual Installation

1. Copy the `custom_components/tr7_exalus` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to **Configuration** → **Integrations** in Home Assistant
2. Click the **"+"** button to add a new integration
3. Search for "TR7 Exalus"
4. Enter your TR7 system details:
   - **Host**: IP address of your TR7 system (e.g., `192.168.1.160`)
   - **Email**: Your TR7 account email
   - **Password**: Your TR7 account password

## Usage

Once configured, your blinds will appear as cover entities in Home Assistant. You can:

- **Open**: Set blinds to fully open position
- **Close**: Set blinds to fully closed position
- **Stop**: Stop blind movement
- **Set Position**: Set blinds to a specific position (0-100%)

### Example Automation

```yaml
automation:
  - alias: "Close blinds at sunset"
    trigger:
      platform: sun
      event: sunset
    action:
      service: cover.close_cover
      target:
        entity_id: cover.tr7_blind_12345678
```

### Example Lovelace Card

```yaml
type: entities
entities:
  - entity: cover.tr7_blind_12345678
    name: "Living Room Blind"
```

## API Reference

This integration communicates with the TR7 Exalus system using WebSocket messages:

### Authentication
```json
{
  "TransactionId": "uuid",
  "Data": {
    "Email": "your-email@example.com",
    "Password": "your-password"
  },
  "Resource": "/users/user/login",
  "Method": 3
}
```

### Position Control
```json
{
  "TransactionId": "uuid",
  "Data": {
    "DeviceGuid": "device-guid",
    "Position": 50
  },
  "Resource": "/devices/device/set_position",
  "Method": 1
}
```

## Troubleshooting

### Connection Issues
- Verify the TR7 system IP address is correct
- Ensure Home Assistant can reach the TR7 system on port 81
- Check that WebSocket connections are not blocked by firewall

### Authentication Issues
- Verify email and password are correct
- Check TR7 system logs for authentication errors

### Debug Logging
Add to your `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.tr7_exalus: debug
```

## Known Limitations

- Position reporting may be inverted (TR7 uses 0=open, HA expects 0=closed)
- Movement state detection (opening/closing) not yet implemented
- Device names are auto-generated from device GUID

## Contributing

Contributions are welcome! Please feel free to submit pull requests or open issues.

## License

This project is licensed under the MIT License.
