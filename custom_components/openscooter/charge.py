"""Smart charging.

The scooter cannot be switched on while it charges, so charging is followed through the smart plug:
 - arrival at home with a low battery -> plug on
 - plug power >= MIN_ON_W -> a session starts, its energy (kWh) is integrated
 - power stays below `off_power_w` for `off_delay_min` -> charger finished (taper) -> plug off, and the
   session teaches the "kWh per battery %" of this charger/battery
 - once that is learned, charging also stops when the energy for the chosen target % has been delivered.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from .const import (
    CONF_ENERGY, CONF_OFF_MIN, CONF_OFF_W, CONF_PLUG, CONF_POWER, CONF_START_BELOW, CONF_TARGET,
    DEFAULT_OFF_MIN, DEFAULT_OFF_W, DEFAULT_START_BELOW, DEFAULT_TARGET, EVENT_CHARGE_FINISHED,
    EVENT_CHARGE_STARTED,
)

if TYPE_CHECKING:
    from .scooter import ScooterHub

_LOGGER = logging.getLogger(__name__)

TICK = timedelta(seconds=15)
MIN_ON_W = 15.0                 # plug power that counts as "charger is delivering"
WAIT_FOR_POWER = timedelta(minutes=15)
MAX_SESSION = timedelta(hours=14)
MIN_LEARN_KWH = 0.03
MIN_LEARN_GAP = 15              # start battery must be <= 85 % to learn from a full charge


class ChargeManager:
    def __init__(self, hub: "ScooterHub") -> None:
        self.hub = hub
        self.hass = hub.hass
        self.auto_enabled = True            # the "Smart charging" switch
        self.status = "idle"                # idle | waiting | charging | finished
        self.session: dict[str, Any] | None = None
        self.last_session: dict[str, Any] | None = None
        self.samples: list[float] = []
        self.kwh_per_percent: float | None = None
        self.estimate: float | None = None  # battery % estimated through charging
        self.away_seen = True
        self._waiting_since: datetime | None = None
        self._power_ticks = 0
        self._unsub = None

    # ---- persistence ---------------------------------------------------------------------------
    def load(self, d: dict[str, Any]) -> None:
        self.auto_enabled = d.get("auto_enabled", True)
        self.samples = [float(x) for x in d.get("samples", [])]
        self.kwh_per_percent = d.get("kwh_per_percent")
        self.last_session = d.get("last_session")
        self.estimate = d.get("estimate")

    def dump(self) -> dict[str, Any]:
        return {
            "auto_enabled": self.auto_enabled, "samples": self.samples,
            "kwh_per_percent": self.kwh_per_percent, "last_session": self.last_session,
            "estimate": self.estimate,
        }

    # ---- options -------------------------------------------------------------------------------
    @property
    def _o(self):
        return self.hub.entry.options

    @property
    def plug(self) -> str | None:
        return self._o.get(CONF_PLUG)

    @property
    def target(self) -> int:
        return int(self._o.get(CONF_TARGET, DEFAULT_TARGET))

    @property
    def enabled(self) -> bool:
        return bool(self.auto_enabled and self.plug)

    async def async_start(self) -> None:
        self._unsub = async_track_time_interval(self.hass, self._tick, TICK)

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    # ---- inputs --------------------------------------------------------------------------------
    def _at_home(self) -> bool | None:
        lat, lon = self.hub.data.get("lat"), self.hub.data.get("lon")
        zone = self.hass.states.get("zone.home")
        if lat is None or lon is None or zone is None:
            return None
        try:
            d = distance(float(lat), float(lon), float(zone.attributes["latitude"]), float(zone.attributes["longitude"]))
        except (KeyError, TypeError, ValueError):
            return None
        radius = max(float(zone.attributes.get("radius", 100)), 80.0)
        return d is not None and d <= radius

    def _float_state(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None or st.state in ("unknown", "unavailable", ""):
            return None
        try:
            return float(st.state)
        except ValueError:
            return None

    @callback
    def on_fresh_battery(self) -> None:
        self.estimate = None  # the scooter itself reported a battery level again

    @callback
    def on_scooter_update(self) -> None:
        home = self._at_home()
        if home is False:
            self.away_seen = True
        if not self.enabled or self.status not in ("idle", "finished") or home is not True or not self.away_seen:
            return
        battery = self.hub.data.get("battery")
        if battery is not None and battery < int(self._o.get(CONF_START_BELOW, DEFAULT_START_BELOW)):
            self.hass.async_create_task(self.async_begin("arrival"))

    # ---- actions -------------------------------------------------------------------------------
    async def async_begin(self, reason: str = "manual") -> None:
        if not self.plug:
            return
        await self.hass.services.async_call("switch", "turn_on", {"entity_id": self.plug}, blocking=False)
        self.status = "waiting"
        self._waiting_since = dt_util.utcnow()
        self.session = None
        self.away_seen = False
        self.hub.notify()
        _LOGGER.info("Smart charging: plug on (%s)", reason)

    async def async_end(self, reason: str) -> None:
        """Stop now: plug off, record the session, learn from it."""
        if self.plug:
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": self.plug}, blocking=False)
        s = self.session
        if s is None:
            self.status = "idle"
            self.hub.notify()
            return
        energy = round(s["energy"], 3)
        start_pct = s.get("start_percent")
        end_pct: float | None = None
        if reason == "taper":
            end_pct = 100.0
        elif reason == "target":
            end_pct = float(self.target)
        if reason == "taper" and start_pct is not None:
            gap = 100.0 - float(start_pct)
            if gap >= MIN_LEARN_GAP and energy >= MIN_LEARN_KWH:
                self.samples = (self.samples + [energy / gap])[-15:]
                self.kwh_per_percent = round(statistics.median(self.samples), 5)
        self.last_session = {
            "start": s["start"], "end": dt_util.utcnow().isoformat(), "start_percent": start_pct,
            "end_percent": end_pct, "energy_kwh": energy, "reason": reason,
        }
        self.estimate = end_pct
        self.session = None
        self.status = "finished"
        self.hass.bus.async_fire(EVENT_CHARGE_FINISHED, self.last_session)
        self.hub.notify()
        self.hub.save_soon()
        _LOGGER.info("Smart charging finished (%s): %.3f kWh", reason, energy)

    def _start_session(self, now: datetime, power: float) -> None:
        e = self._float_state(self._o.get(CONF_ENERGY))
        self.session = {
            "start": now.isoformat(), "start_percent": self.hub.data.get("battery"), "energy": 0.0,
            "last_ts": now.isoformat(), "last_power": power, "e_start": e, "low_since": None,
        }
        self.status = "charging"
        self._power_ticks = 0
        self.hass.bus.async_fire(EVENT_CHARGE_STARTED, {"start_percent": self.session["start_percent"]})
        self.hub.notify()

    # ---- the 15 s evaluation -------------------------------------------------------------------
    async def _tick(self, _now=None) -> None:
        power = self._float_state(self._o.get(CONF_POWER))
        now = dt_util.utcnow()

        # A charge started by hand (plug switched on outside the integration) is picked up too, so it is
        # also switched off at the end and also taught to the model.
        if self.status in ("idle", "finished") and self.enabled:
            if power is not None and power >= MIN_ON_W:
                self._power_ticks += 1
                if self._power_ticks >= 2:
                    self._start_session(now, power)
                    return
            else:
                self._power_ticks = 0

        if self.status == "waiting":
            if power is not None and power >= MIN_ON_W:
                self._start_session(now, power)
            elif self._waiting_since and now - self._waiting_since > WAIT_FOR_POWER:
                if self.plug:
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": self.plug}, blocking=False)
                self.status = "idle"
                self.hub.notify()
            return

        if self.status != "charging" or self.session is None:
            return
        s = self.session
        last_ts = dt_util.parse_datetime(s["last_ts"]) or now
        hours = max(0.0, (now - last_ts).total_seconds() / 3600.0)
        if power is not None:
            s["energy"] += (s.get("last_power", power) + power) / 2.0 * hours / 1000.0   # trapezoid, kWh
            s["last_power"] = power
        e_now = self._float_state(self._o.get(CONF_ENERGY))
        if e_now is not None and s.get("e_start") is not None and e_now >= s["e_start"]:
            s["energy"] = max(s["energy"], e_now - s["e_start"]) if power is None else (e_now - s["e_start"])
        s["last_ts"] = now.isoformat()

        k = self.kwh_per_percent
        sp = s.get("start_percent")
        if k and sp is not None:
            self.estimate = round(min(100.0, float(sp) + s["energy"] / k), 1)
        elif sp is not None:
            self.estimate = None

        off_w = float(self._o.get(CONF_OFF_W, DEFAULT_OFF_W))
        off_min = float(self._o.get(CONF_OFF_MIN, DEFAULT_OFF_MIN))
        started = dt_util.parse_datetime(s["start"]) or now

        if power is not None and power < off_w and now - started > timedelta(minutes=5):
            if s["low_since"] is None:
                s["low_since"] = now.isoformat()
            low = dt_util.parse_datetime(s["low_since"]) or now
            if now - low >= timedelta(minutes=off_min):
                await self.async_end("taper")
                return
        else:
            s["low_since"] = None

        if k and sp is not None and len(self.samples) >= 2 and self.target < 100:
            if s["energy"] >= max(0.0, (self.target - float(sp))) * k:
                await self.async_end("target")
                return
        if now - started > MAX_SESSION:
            await self.async_end("timeout")
            return
        self.hub.notify()
