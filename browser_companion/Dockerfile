FROM jlesage/chromium:latest

RUN apk add --no-cache \
    python3 \
    py3-aiohttp \
    xdg-utils \
    desktop-file-utils \
    curl

COPY rootfs/ /

RUN chmod a+x \
    /etc/cont-init.d/90-companion.sh \
    /etc/services.d/companion/run \
    /opt/companion/capture-url.sh
