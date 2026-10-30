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
from .rangemodel import RangeModel
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
CONNECTED_TIMEOUT = 180   # seconds: the app sends at least every minute; silence means the phone is gone
STALE_BATTERY_H = 12      # a battery level older than this is not trusted as the start of a charge
KEEP_FULL_TRACKS = 10     # complete routes kept for the newest rides
SKETCH_POINTS = 60        # older rides keep only a simplified route (summaries stay forever)
MAX_SKETCHES = 3000
MAX_TRACK_POINTS = 2500


class ScooterHub:
    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.data: dict[str, Any] = {}
        self._connected = False
        self._conn_state = False
        self.battery_ts: datetime | None = None
        self.last_seen: datetime | None = None
        self.store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.charge = ChargeManager(self)
        self.range = RangeModel()
        # The database part: every ride, every range-learning observation and the latest model of the app.
        # Kept in its own file so it can grow without slowing the live state down.
        self.dstore: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.data")
        self.rides: dict[str, dict[str, Any]] = {}
        self.segments: list[str] = []
        self._seg_keys: set[str] = set()
        self.tstore: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tracks")
        self.tracks: dict[str, list] = {}    # newest rides: complete route [[lat, lon, time_ms, kmh], ...]
        self.sketches: dict[str, list] = {}  # older rides: simplified route (about 60 points)
        self.model: dict[str, Any] = {}
        self.data_synced: datetime | None = None

    @property
    def connected(self) -> bool:
        """The scooter is connected to the phone *and* the phone is still talking to us (no stale "connected")."""
        if not self._connected or self.last_seen is None:
            return False
        return (dt_util.utcnow() - self.last_seen).total_seconds() < CONNECTED_TIMEOUT

    @callback
    def check_connection(self) -> None:
        """Called by the 15 s tick: tell the entities when "connected" expired without any message arriving."""
        now = self.connected
        if now != self._conn_state:
            self._conn_state = now
            self.notify()

    @property
    def battery_age_min(self) -> float | None:
        return round((dt_util.utcnow() - self.battery_ts).total_seconds() / 60.0) if self.battery_ts else None

    @property
    def battery_best(self) -> dict[str, Any] | None:
        """The best known battery level and where it comes from: live from the scooter, the charge estimate while it
        charges (works without the phone), the level it was charged to, or the last level seen."""
        c = self.charge
        if self.connected and self.data.get("battery") is not None:
            return {"value": float(self.data["battery"]), "source": "live", "age_min": 0}
        if c.status == "charging" and c.estimate is not None:
            return {"value": float(c.estimate), "source": "charging estimate", "age_min": 0}
        # A charge that ended after the last real reading from the scooter: the level it was charged to is newer
        # than 82 % seen before the charge. The scooter's next real reading (newer again) replaces it.
        if c.status != "charging" and c.history:
            rec = c.history[-1]
            end = dt_util.parse_datetime(rec.get("end") or "")
            if rec.get("end_percent") is not None and end is not None and (self.battery_ts is None or end > self.battery_ts):
                return {"value": float(rec["end_percent"]), "source": "charged (estimated)", "age_min": None}
        if c.status in ("finished", "waiting", "idle") and c.last_estimate is not None:   # until the scooter reports a level again
            return {"value": float(c.last_estimate), "source": "charged (estimated)", "age_min": None}
        if self.data.get("battery") is not None:
            return {"value": float(self.data["battery"]), "source": "last seen", "age_min": self.battery_age_min}
        return None

    def range_for(self, mode: str | None = None, full: bool = False) -> float | None:
        """Range in km for a mode (default: the last known mode) on the best known battery (or 100 %)."""
        mode = mode or self.data.get("mode") or "drive"
        if full:
            return self.range.range_km(100, mode)
        best = self.battery_best
        return self.range.range_km(best["value"], mode) if best else None

    @property
    def signal(self) -> str:
        return SIGNAL_UPDATE.format(self.entry.entry_id)

    async def async_load(self) -> None:
        saved = await self.store.async_load() or {}
        self.data = saved.get("data", {})
        self._connected = False  # never trust "connected" after a restart
        if (ts := saved.get("battery_ts")):
            self.battery_ts = dt_util.parse_datetime(ts)
        if (ts := saved.get("last_seen")):
            self.last_seen = dt_util.parse_datetime(ts)
        self.charge.load(saved.get("charge", {}))
        d = await self.dstore.async_load() or {}
        self.rides = d.get("rides", {})
        self.segments = d.get("segments", [])
        self._seg_keys = {r.split(",", 1)[0] for r in self.segments}
        self.model = d.get("model", {})
        self.range.fit(self.segments)
        td = await self.tstore.async_load() or {}
        self.tracks = td.get("tracks", {})
        self.sketches = td.get("sketches", {})
        self._enforce_track_policy()
        if (ts := d.get("synced")):
            self.data_synced = dt_util.parse_datetime(ts)

    def _dump(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "battery_ts": self.battery_ts.isoformat() if self.battery_ts else None,
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
        self._connected = bool(payload.get("connected", False))
        self.last_seen = dt_util.utcnow()
        if self._connected and payload.get("battery") is not None:
            self.battery_ts = self.last_seen
        if self._connected:
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
            self.hass.async_add_executor_job(self.range.fit, list(self.segments))
        self._data_changed()
        return added

    @callback
    def merge_track(self, data: Any) -> bool:
        """Store the GPS route of one ride (what the map in the panel and the GPX export use)."""
        if not isinstance(data, dict) or "id" not in data or not isinstance(data.get("points"), list):
            return False
        pts = []
        for p in data["points"][:MAX_TRACK_POINTS]:
            if isinstance(p, list) and len(p) >= 2:
                try:
                    pts.append([float(p[0]), float(p[1]), int(p[2]) if len(p) > 2 else 0, float(p[3]) if len(p) > 3 else 0.0])
                except (TypeError, ValueError):
                    continue
        if len(pts) < 2:
            return False
        self.tracks[str(data["id"])] = pts
        self.sketches.pop(str(data["id"]), None)
        self._enforce_track_policy()
        self.tstore.async_delay_save(lambda: {"tracks": self.tracks, "sketches": self.sketches}, 30)
        self.notify()
        return True

    def _ride_time(self, key: str) -> int:
        r = self.rides.get(key)
        if r and r.get("start"):
            return int(r["start"])
        return int(key) if key.isdigit() else 0

    def _enforce_track_policy(self) -> None:
        """Complete routes only for the newest rides; older ones shrink to a simplified route."""
        keys = sorted(set(self.tracks) | set(self.sketches), key=self._ride_time, reverse=True)
        for k in keys[KEEP_FULL_TRACKS:]:
            if k in self.tracks:
                self.sketches[k] = simplify_route(self.tracks.pop(k), SKETCH_POINTS)
        for k in keys[KEEP_FULL_TRACKS:][MAX_SKETCHES:]:
            self.sketches.pop(k, None)

    @callback
    def set_model(self, model: Any) -> None:
        if isinstance(model, dict):
            self.model = model
            self._data_changed()

    def restore_payload(self) -> dict[str, Any]:
        """Everything a fresh install needs to continue where the old one stopped."""
        return {"rides": list(self.rides.values()), "segments": self.segments, "model": self.model,
                "charges": self.charge.history[-60:], "tracks": {**self.sketches, **self.tracks}}


def simplify_route(pts: list, max_points: int) -> list:
    """Douglas-Peucker, tightening until at most max_points remain (keeps the first and the last point)."""
    if len(pts) <= max_points:
        return pts
    import math

    ky = 110540.0
    kx = 111320.0 * math.cos(math.radians(pts[0][0]))
    xy = [(p[1] * kx, p[0] * ky) for p in pts]

    def dist(i: int, a: int, b: int) -> float:
        (x, y), (x1, y1), (x2, y2) = xy[i], xy[a], xy[b]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy)
        return math.hypot(x - x1, y - y1) if n == 0 else abs(dx * (y1 - y) - dy * (x1 - x)) / n

    eps = 2.0
    for _ in range(40):
        keep = {0, len(pts) - 1}
        stack = [(0, len(pts) - 1)]
        while stack:
            s, e = stack.pop()
            md, mi = 0.0, -1
            for i in range(s + 1, e):
                d = dist(i, s, e)
                if d > md:
                    md, mi = d, i
            if mi >= 0 and md > eps:
                keep.add(mi)
                stack += [(s, mi), (mi, e)]
        if len(keep) <= max_points:
            return [pts[i] for i in sorted(keep)]
        eps *= 1.6
    step = max(1, len(pts) // max_points)
    return pts[::step] + [pts[-1]]
