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
    "range_km", "range_low_km", "range_high_km", "range_full_km", "range_samples", "range_error_pct",
)
RIDE_KEYS = ("id", "start", "end", "dist", "moving", "max", "avg", "b0", "b1")
MAX_RIDES = 3000
MAX_SEGMENTS = 50000


class ScooterHub:
    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.data: dict[str, Any] = {}
        self.connected = False
        self.last_seen: datetime | None = None
        self.store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.charge = ChargeManager(self)
        # The database part: every ride, every range-learning observation and the latest model of the app.
        # Kept in its own file so it can grow without slowing the live state down.
        self.dstore: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.data")
        self.rides: dict[str, dict[str, Any]] = {}
        self.segments: list[str] = []
        self._seg_keys: set[str] = set()
        self.model: dict[str, Any] = {}
        self.data_synced: datetime | None = None

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
        d = await self.dstore.async_load() or {}
        self.rides = d.get("rides", {})
        self.segments = d.get("segments", [])
        self._seg_keys = {r.split(",", 1)[0] for r in self.segments}
        self.model = d.get("model", {})
        if (ts := d.get("synced")):
            self.data_synced = dt_util.parse_datetime(ts)

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

    # ---- the data store ------------------------------------------------------------------------
    def _dump_data(self) -> dict[str, Any]:
        return {"rides": self.rides, "segments": self.segments, "model": self.model,
                "synced": self.data_synced.isoformat() if self.data_synced else None}

    @callback
    def _data_changed(self) -> None:
        self.data_synced = dt_util.utcnow()
        self.dstore.async_delay_save(self._dump_data, 30)
        self.notify()

    @callback
    def merge_rides(self, rows: Any) -> int:
        """Store ride summaries sent by the app (idempotent: the ride id is the key)."""
        if not isinstance(rows, list):
            return 0
        added = 0
        for r in rows:
            if isinstance(r, dict) and all(k in r for k in ("id", "start", "end")):
                key = str(r["id"])
                added += key not in self.rides
                self.rides[key] = {k: r.get(k) for k in RIDE_KEYS}
        if len(self.rides) > MAX_RIDES:
            for k in sorted(self.rides, key=lambda x: self.rides[x].get("start") or 0)[: len(self.rides) - MAX_RIDES]:
                self.rides.pop(k, None)
        self._data_changed()
        return added

    @callback
    def merge_segments(self, data: Any) -> int:
        """Store range-learning observations (one CSV line each; the first column, the time, is the key)."""
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return 0
        added = 0
        for line in rows:
            if not isinstance(line, str) or line.count(",") < 10:
                continue
            key = line.split(",", 1)[0]
            if key in self._seg_keys:
                continue
            self._seg_keys.add(key)
            self.segments.append(line)
            added += 1
        if len(self.segments) > MAX_SEGMENTS:
            self.segments = self.segments[-MAX_SEGMENTS:]
            self._seg_keys = {r.split(",", 1)[0] for r in self.segments}
        if added:
            self.segments.sort(key=lambda r: int(float(r.split(",", 1)[0])) if r.split(",", 1)[0].replace(".", "", 1).isdigit() else 0)
        self._data_changed()
        return added

    @callback
    def set_model(self, model: Any) -> None:
        if isinstance(model, dict):
            self.model = model
            self._data_changed()

    def restore_payload(self) -> dict[str, Any]:
        """Everything a fresh install needs to continue where the old one stopped."""
        return {"rides": list(self.rides.values()), "segments": self.segments, "model": self.model,
                "charges": self.charge.history[-60:]}
