"""Copy the extension's icon and screenshots into docs/assets so the documentation site can show them.

    python tools/docs_assets.py

The images live in editors/vscode/media (the Marketplace page uses them too). The copies under docs/assets
are build output and are ignored by git. Run this before `zensical serve` or `zensical build`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "editors" / "vscode" / "media"
ASSETS = ROOT / "docs" / "assets"


def main() -> None:
    shots = ASSETS / "screenshots"
    shutil.rmtree(shots, ignore_errors=True)
    shots.mkdir(parents=True)
    for png in sorted((MEDIA / "screenshots").glob("*.png")):
        shutil.copy2(png, shots / png.name)
    shutil.copy2(MEDIA / "icon.png", ASSETS / "icon.png")
    print(f"copied icon and {len(list(shots.iterdir()))} screenshots to {ASSETS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
