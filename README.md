# Hue Lights

Omarchy bar widget for Philips Hue. Control rooms, zones and single lights (on/off,
brightness) and recall scenes straight from the bar, via your local Hue bridge.

- **Left click** the bulb: open the panel. **Right click**: switch all lights off.
- Click a room name to expand its scenes and single lights.
- The bulb is lit (accent colour) while any light is on.

## Pairing

1. Open the panel. Bridges on your network are found automatically via mDNS
   (or enter the bridge IP by hand).
2. Click **Pair**, then press the round link button on top of the bridge within 30 s.

## Privacy & security

- Local only: talks to the bridge's CLIP v2 API over HTTPS on your LAN. No cloud,
  no Philips account, no other network traffic.
- The bridge certificate is pinned when pairing (SHA-256 fingerprint and
  CN = bridge id) and checked on every connection.
- The app key is stored in `~/.config/omarchy/hue-lights/config.json` (mode 0600).
  **Unpair** deletes it. To revoke it on the bridge too, remove the
  "omarchy_hue_lights" app in the Hue app (Settings > Apps).
- If the bridge gets a new IP address, it is found again by its bridge id.

## Requirements

`python3` (standard library only) and `avahi-browse` for discovery.

## Tests

    python3 -m unittest discover -s tests
