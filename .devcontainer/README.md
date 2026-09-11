# Devcontainer with Supervisor

This uses the official Home Assistant **apps** image (`ghcr.io/home-assistant/devcontainer:6-apps`). It runs **Supervisor + Home Assistant Core** with Docker-in-Docker, same as add-on development at [Local app testing](https://developers.home-assistant.io/docs/apps/testing/).

That is the only practical way to exercise Ingress, the add-on store, and slug discovery (`local_browser_companion`) together. A plain HA Container install has no Supervisor.

## Requirements

- Docker with permission to run **privileged** containers
- About **8 GB RAM** (Supervisor + HA + Chromium)
- Sibling clones next to this repo (bind mounts):
  - `../moja-biedronka`
  - `../home-assistant-allegro`

## Start

1. Open **this** repository in Cursor / VS Code
2. **Dev Containers: Reopen in Container** (first pull is slow). After adding the Allegro mount: **Rebuild Container**.
3. Terminal → Run Task → **Start Home Assistant** (`supervisor_run`)
4. Wait until it finishes initializing
5. **Rebuild the container** after changing `appPort` (Dev Containers: Rebuild Container). Then open **http://localhost:7123** (mapped to HA's port **80**).

   Recent Supervisor-based Core listens on port 80, not 8123. The old `7123:8123` mapping made the UI redirect the browser to `http://localhost:80`, which was not published. If 7123 still redirects, use **http://localhost:80** after rebuild.
6. Settings → Apps → Local apps → install **Browser Companion** (rebuild if you changed the Dockerfile)
7. Link the integration you want to test (copies into HA `custom_components`; uses `sudo` because that tree is root-owned). Re-run after you change that integration’s code.
   - **Link Biedronka custom component**
   - **Link Allegro custom component**
8. Settings → Devices & services → add **Biedronka** or **Allegro buyer** → Browser Companion

In Supervisor logs you should see `loading service 'companion'` and `[companion] starting session API on :8100`.

## Layout inside the container

| Path | What |
| --- | --- |
| `/mnt/supervisor/apps/local/homeassistant-browser-companion` | this repo (local add-on store) |
| `/mnt/supervisor/homeassistant` | HA config after Supervisor starts |
| `/mnt/moja-biedronka` | sibling Biedronka integration |
| `/mnt/home-assistant-allegro` | sibling Allegro integration |

## Limits

- This is **not** Home Assistant OS on bare metal; nested Docker can be slower and GPU passthrough is unlikely.
- If a sibling mount is missing, drop that entry in `.devcontainer/devcontainer.json` `mounts` and copy `custom_components/…` by hand.
