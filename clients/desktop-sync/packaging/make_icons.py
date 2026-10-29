"""Draws the app's icon (the tray's "up to date" icon, bigger) as icon.png and icon.ico for packaging."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mnemerynne_backup.tray import _icon_image  # noqa: E402

out = Path(__file__).parent / "build"
out.mkdir(exist_ok=True)
img = _icon_image("ok").resize((256, 256))
img.save(out / "icon.png")
img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"wrote {out / 'icon.png'} and {out / 'icon.ico'}")
