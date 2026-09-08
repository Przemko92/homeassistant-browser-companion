# Browser Companion

Generic Chromium add-on. Integrations open a login URL; the user completes the page (including any captcha) in the sidebar browser; the add-on captures a matching redirect and returns it over HTTP.

## Memory

Chromium typically needs about 1 GB of RAM. Disable unused add-ons if the host is tight on memory.

## Ingress

The sidebar panel shows session status and proxies Chromium's web UI. If the embedded view is blank, expose port **5800** in the add-on network settings and open that URL instead.

## API

The integration declares `wait` in `POST /v1/sessions`: which browser event to capture (`http_redirect`, `navigation`, or `protocol_handler`) and how to match it. Biedronka waits for HTTP 302 whose `Location` starts with `app://cma20.biedronka.pl`.
