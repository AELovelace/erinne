"""The built-in Fleetboard API presets (apis.PRESETS): Fleetboard's service API on this box
(http://127.0.0.1:9281/api/v1), as two entries with their own token and group:

- **Fleetboard**: read-only (summary, hosts, host detail, over thresholds, disk forecast,
  history, client audit, activity, thresholds, token check);
- **Fleetboard (changes)**: proposals the person runs (add, rename, pause, resume or remove a
  host, set a threshold), each with a reason Fleetboard's audit log keeps.

They are read from the same JSON files a project would upload (`api-templates/fleetboard.json` and
`fleetboard-actions.json`), through apis.parse_template, so the files and the presets can never
drift apart. If a file is missing or broken the preset is left out and the reason logged; the rest
of Mnemerynne starts as usual.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# The repository's api-templates folder, next to the mnemerynne package (/opt/mnemerynne/api-templates).
TEMPLATES = Path(__file__).resolve().parent.parent / "api-templates"

# (preset key, template file): the keys are what the admin page and APIs made from them refer to.
FILES = (("fleetboard", "fleetboard.json"), ("fleetboard-changes", "fleetboard-actions.json"))


def load(parse_template, files=FILES, label: str = "Fleetboard") -> list[dict]:
    """The Fleetboard presets (or the (key, file) pairs in `files`, e.g. network_presets.FILES),
    each a parsed template with its preset key added. `label` only names them in the log.
    parse_template is apis.parse_template (passed in, since apis imports this module)."""
    out = []
    for key, name in files:
        path = TEMPLATES / name
        try:
            preset = parse_template(path.read_text(encoding="utf-8"))   # the same checks an upload gets
        except Exception as exc:                                         # missing file, bad JSON, refused field
            log.warning("%s preset %s not loaded from %s: %s", label, key, path, exc)
            continue
        out.append({"key": key, **preset})
    return out
