"""OpenScooter: scooter data and smart charging in Home Assistant, fed by the OpenScooter Android app."""
from __future__ import annotations

import hmac
import logging
from pathlib import Path

import voluptuous as vol
from aiohttp import web

from homeassistant.components import frontend, panel_custom, webhook, websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback

from .const import CONF_SECRET, CONF_WEBHOOK_ID, DOMAIN, STATIC_URL, VERSION
from .scooter import ScooterHub

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "binary_sensor", "device_tracker", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = ScooterHub(hass, entry)
    await hub.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub

    async def handle_webhook(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"ok": False, "error": "bad json"}, status=400)
        if not isinstance(body, dict) or not hmac.compare_digest(str(body.get("secret", "")), entry.data[CONF_SECRET]):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        if body.get("type") == "state" and isinstance(body.get("data"), dict):
            hub.update(body["data"])
        c = hub.charge
        return web.json_response({
            "ok": True, "version": VERSION,
            "charge": {
                "status": c.status, "target": c.target, "enabled": c.enabled,
                "estimate": c.estimate, "kwh_per_percent": c.kwh_per_percent,
            },
        })

    webhook.async_register(hass, DOMAIN, "OpenScooter", entry.data[CONF_WEBHOOK_ID], handle_webhook,
                           allowed_methods=["POST"])

    # Frontend: the pairing panel (sidebar) and the dashboard card (loaded on every dashboard automatically).
    if not hass.data[DOMAIN].get("_frontend"):
        hass.data[DOMAIN]["_frontend"] = True
        www = str(Path(__file__).parent / "www")
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, www, False)])
        add_extra_js_url(hass, f"{STATIC_URL}/openscooter-card.js?v={VERSION}")
        websocket_api.async_register_command(hass, ws_pairing)
    await panel_custom.async_register_panel(
        hass, frontend_url_path="openscooter", webcomponent_name="openscooter-panel",
        sidebar_title="OpenScooter", sidebar_icon="mdi:scooter",
        module_url=f"{STATIC_URL}/openscooter-panel.js?v={VERSION}", require_admin=True, config={},
    )

    async def start_charge(call: ServiceCall) -> None:
        await hub.charge.async_begin("service")

    async def stop_charge(call: ServiceCall) -> None:
        await hub.charge.async_end("manual")

    hass.services.async_register(DOMAIN, "start_charge", start_charge)
    hass.services.async_register(DOMAIN, "stop_charge", stop_charge)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await hub.charge.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub: ScooterHub = hass.data[DOMAIN][entry.entry_id]
    await hub.charge.async_stop()
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload:
        webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
        frontend.async_remove_panel(hass, "openscooter")
        hass.services.async_remove(DOMAIN, "start_charge")
        hass.services.async_remove(DOMAIN, "stop_charge")
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload


@websocket_api.websocket_command({vol.Required("type"): "openscooter/pairing"})
@websocket_api.require_admin
@callback
def ws_pairing(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Give the pairing panel what the app needs (the panel adds the address it was opened with)."""
    for entry_id, hub in hass.data.get(DOMAIN, {}).items():
        if isinstance(hub, ScooterHub):
            connection.send_result(msg["id"], {
                "webhook_id": hub.entry.data[CONF_WEBHOOK_ID],
                "secret": hub.entry.data[CONF_SECRET],
                "last_seen": hub.last_seen.isoformat() if hub.last_seen else None,
                "connected": hub.connected,
                "battery": hub.data.get("battery"),
            })
            return
    connection.send_error(msg["id"], "not_found", "OpenScooter is not set up")
