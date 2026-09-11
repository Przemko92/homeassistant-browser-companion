"""Deprecated copy-paste helper.

Install the client instead::

    pip install ha-browser-companion

This module re-exports the public HTTP API so older integrations that copied
``python/examples/homeassistant_client.py`` keep working when the package is installed.
"""

from ha_browser_companion.client import *  # noqa: F403
from ha_browser_companion.client import (  # noqa: F401
    API_PORT,
    DEFAULT_TIMEOUT,
    FALLBACK_BASE_URL,
    FALLBACK_SLUG,
    HEALTH_TIMEOUT,
    KNOWN_SLUGS,
    SLUG_SUFFIX,
    BrowserCompanionClient,
    CompanionEndpoint,
    CompanionError,
    addon_is_available,
    addon_state,
    async_create_session,
    async_delete_session,
    async_discover_base_url,
    async_discover_companion,
    async_get_session,
    async_health_ok,
    async_wait_captured,
    base_url_from_slug,
    candidate_base_urls,
    captured_cookie,
    captured_query,
    companion_placeholders,
    companion_redirect_href,
    companion_ui_url,
    hostname_from_addon_slug,
    ingress_path,
    is_companion_slug,
    slug_from_api_base,
    supervisor_present,
)
