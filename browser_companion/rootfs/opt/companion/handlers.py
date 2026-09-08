"""Register xdg-open handlers for custom URL schemes."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

DESKTOP_DIR = Path(
    os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
) / "applications"
DESKTOP_FILE = DESKTOP_DIR / "companion-capture.desktop"
CAPTURE_SCRIPT = "/opt/companion/capture-url.sh"


def register_schemes(schemes: list[str]) -> None:
    unique = [item.strip().lower() for item in schemes if item and item.strip()]
    if not unique:
        return
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    mime = ";".join(f"x-scheme-handler/{scheme}" for scheme in unique) + ";"
    DESKTOP_FILE.write_text(
        "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                "Name=Browser Companion Capture",
                "NoDisplay=true",
                f"Exec={CAPTURE_SCRIPT} %u",
                f"MimeType={mime}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(CAPTURE_SCRIPT, 0o755)
    for scheme in unique:
        subprocess.run(
            [
                "xdg-mime",
                "default",
                DESKTOP_FILE.name,
                f"x-scheme-handler/{scheme}",
            ],
            check=False,
            capture_output=True,
        )
    subprocess.run(
        ["update-desktop-database", str(DESKTOP_DIR)],
        check=False,
        capture_output=True,
    )
