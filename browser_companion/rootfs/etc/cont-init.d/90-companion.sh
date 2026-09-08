#!/bin/sh
set -eu
mkdir -p /config/.local/share/applications
chmod a+x /opt/companion/capture-url.sh /etc/services.d/companion/run
echo "[companion] init done"
