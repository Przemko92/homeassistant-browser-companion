# Browser Companion

Generic Chromium in the Home Assistant sidebar. An integration opens a login URL; **you** complete the page (including captcha or 2FA); the add-on captures a matching redirect, navigation, or custom-scheme URL and returns it over HTTP.

It does not solve captchas or fill forms.

## Requirements

Home Assistant OS or Supervised only. Chromium typically needs about **1 GB of RAM**.

## Install

Install from the add-on store after adding this repository, then **Start**. Chromium appears in the sidebar for admin users.

After an update, open the add-on and click **Rebuild**. Logs should show the session API on port **8100**.

## Ingress vs API

| Port | Role |
| --- | --- |
| **5800** | Chromium web UI (sidebar / Ingress) |
| **8100** | Session API for custom components |

If the sidebar is blank, expose port **5800** in the add-on network settings. Keep **8100 unpublished** on the host: the API has no authentication and is meant for the Supervisor network only.

Login runs in an **incognito** profile. After a match, Chromium shows a success dialog. **Close** wipes the tab; **Keep open** only hides the overlay. Only one session at a time; a new request replaces the previous one.

## Session API (for integrations)

Base URL: `http://{addon-hostname}:8100`  
Hostname = add-on slug with `_` → `-` (for example `local-browser-companion`).

Typical flow:

1. `GET /v1/health` — discover / liveness
2. `POST /v1/sessions` with `start_url` and `wait`
3. Poll `GET /v1/sessions/{id}` until `captured`, `expired`, or `error`
4. Optional `DELETE /v1/sessions/{id}`

`wait.event` is one of:

- `http_redirect` — HTTP 3xx `Location` (prefixes and/or schemes)
- `navigation` — page finished loading on a matching URL
- `protocol_handler` — custom URL scheme via `xdg-open`

Optional `wait.cookies` returns named cookies at capture time. Optional `navigate_after` navigates to a URL once listed cookies exist.

Python helper for custom components: install [`ha-browser-companion`](https://pypi.org/project/ha-browser-companion/) (`pip install ha-browser-companion`) and add it to `manifest.json` `requirements`. Config-flow mixin and HTTP client: [package README](https://github.com/Przemko92/homeassistant-browser-companion/blob/master/python/README.md).

Full request/response reference, error codes, and copy-paste examples: [README on GitHub](https://github.com/Przemko92/homeassistant-browser-companion#session-api).
