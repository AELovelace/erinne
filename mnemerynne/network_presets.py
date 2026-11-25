"""The built-in network API presets (apis.PRESETS), both read-only:

- **UniFi Network**: one UniFi console's own Integration API (sites, devices, live device
  statistics, clients without their names), `api-templates/unifi-network.json`;
- **OPNsense**: one OPNsense firewall's REST API (status, firmware, gateways, interfaces,
  rules, port forwards, aliases, services, IDS, ARP, firewall log), `api-templates/opnsense.json`.

Both ship with a placeholder `.example` address: the admin sets the real one, the key and the
pinned certificate after adding. They're read through apis.parse_template like Fleetboard's
(fleetboard_presets.load), so a file and its preset can never drift apart; a broken file is left
out and logged, and the rest of Mnemerynne starts as usual.
"""

from __future__ import annotations

from mnemerynne import fleetboard_presets

# (preset key, template file in api-templates/): the keys are what the admin page refers to.
FILES = (("unifi-network", "unifi-network.json"), ("opnsense", "opnsense.json"))


def load(parse_template) -> list[dict]:
    """The UniFi Network and OPNsense presets, each a parsed template with its key added."""
    return fleetboard_presets.load(parse_template, FILES, "Network")   # same loader, same checks, our files
