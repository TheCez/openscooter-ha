"""Shared state of the scooter, fed by the app through the webhook."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .charge import ChargeManager
from .const import DOMAIN, SIGNAL_UPDATE

_LOGGER = logging.getLogger(__name__)

# Fields the app may send (anything else is ignored). None means "unknown": the last value is kept.
FIELDS = (
    "battery", "speed", "locked", "headlight", "mode", "voltage", "current", "motor_temp",
    "scooter_temp", "odometer", "trip_km", "lat", "lon", "accuracy", "error_code", "app_version",
)


class ScooterHub:
    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.data: dict[str, Any] = {}
        self.connected = False
        self.last_seen: datetime | None = None
        self.store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.charge = ChargeManager(self)

    @property
    def signal(self) -> str:
        return SIGNAL_UPDATE.format(self.entry.entry_id)

    async def async_load(self) -> None:
        saved = await self.store.async_load() or {}
        self.data = saved.get("data", {})
        self.connected = False  # never trust "connected" after a restart
        if (ts := saved.get("last_seen")):
            self.last_seen = dt_util.parse_datetime(ts)
        self.charge.load(saved.get("charge", {}))

    def _dump(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "charge": self.charge.dump(),
        }

    @callback
    def save_soon(self) -> None:
        self.store.async_delay_save(self._dump, 20)

    @callback
    def notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def update(self, payload: dict[str, Any]) -> None:
        """Merge one state message from the app."""
        for key in FIELDS:
            if payload.get(key) is not None:
                self.data[key] = payload[key]
        self.connected = bool(payload.get("connected", False))
        self.last_seen = dt_util.utcnow()
        if self.connected:
            self.charge.on_fresh_battery()
        self.notify()
        self.charge.on_scooter_update()
        self.save_soon()
