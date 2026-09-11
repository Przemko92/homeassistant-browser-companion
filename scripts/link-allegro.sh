#!/usr/bin/env bash
# Copy the sibling home-assistant-allegro integration into Supervisor's HA config.
#
# Must copy (not symlink): Core runs in nested Docker and only sees
# /mnt/supervisor/homeassistant as /config. A link to /mnt/home-assistant-allegro
# would be broken inside that container. Config dirs are root-owned.
set -euo pipefail

SRC="${ALLEGRO_SRC:-/mnt/home-assistant-allegro/custom_components/allegro}"
if [[ ! -d "$SRC" ]]; then
  echo "Allegro not found at $SRC"
  echo "Keep the repos as siblings: GIT/home-assistant-allegro and GIT/homeassistant-browser-companion"
  exit 1
fi

DEST_PARENT=""
for candidate in \
  /mnt/supervisor/homeassistant \
  /mnt/data/supervisor/homeassistant
do
  if [[ -d "$candidate" ]]; then
    DEST_PARENT="$candidate"
    break
  fi
done

if [[ -z "$DEST_PARENT" ]]; then
  echo "Home Assistant config not found. Run the task 'Start Home Assistant' first and wait until onboarding is up."
  exit 1
fi

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "Need write access to $DEST_PARENT (permission denied as $(id -un))."
    exit 1
  fi
}

DEST="$DEST_PARENT/custom_components/allegro"
as_root mkdir -p "$DEST_PARENT/custom_components"
as_root rm -rf "$DEST"
as_root cp -a "$SRC" "$DEST"
as_root chmod -R a+rX "$DEST"
echo "Copied $SRC -> $DEST"
echo "Re-run this task after changing Allegro code."

if command -v ha >/dev/null 2>&1; then
  ha core restart || true
  echo "Requested ha core restart."
else
  echo "Restart Home Assistant from Settings → System → Restart so it loads the integration."
fi
