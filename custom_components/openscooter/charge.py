"""Smart charging.

The scooter cannot be switched on while it charges, so charging is followed through the smart plug:
 - arrival at home with a low battery -> plug on (a charge started by hand is picked up too)
 - plug power >= MIN_ON_W -> a session starts; its energy (kWh) is read from the plug (or integrated from power)
 - power stays below `off_power_w` for `off_delay_min` -> the charger has finished (CV taper) -> plug off
 - every finished charge is stored (with a coarse power curve) and refits the charge model.

Charge model (explainable, works with a handful of sessions): energy to fill a battery from s % is
        E_full(s) = a + b * (100 - s)                                   [kWh]
where b is the energy of one % in the constant-current part and a the extra energy of the constant-voltage
tail and charger losses. Fitted by least squares over the stored sessions (with a single session: a = 0).
The last TAIL_PCT percent are spread over the energy `a + b*TAIL_PCT` (they cost more energy per %).
From the energy delivered so far this gives the live battery level, the time left, and the stop point for a
"charge up to X %" target. All numbers come from this scooter's own charges and sharpen with every one.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

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

STALE_BATTERY_H = 12
TICK = timedelta(seconds=15)
BACKFILL = timedelta(hours=12)       # how far back the real charge start is searched
END_FRACTION = 0.12                # the charger is done when power falls under this share of the session's peak
END_MAX_W = 30.0
MIN_ON_W = 15.0                    # plug power that counts as "the charger is delivering"
WAIT_FOR_POWER = timedelta(minutes=15)
MAX_SESSION = timedelta(hours=14)
MAX_INTEGRATE_H = 0.1              # never integrate power over a gap longer than 6 min (e.g. a restart)
TAIL_PCT = 10.0                    # the last 10 % are the slow constant-voltage part
MIN_LEARN_KWH = 0.03
MIN_LEARN_GAP = 15                 # a charge teaches the model only if it started at <= 85 %
MIN_LEARN_MIN = 10
HISTORY_MAX = 200
CURVE_STEP_S = 120
CURVE_MAX = 400
STOP_MODEL_SESSIONS = 3            # "charge up to X %" is only enforced once the model has this many sessions
DEFAULT_TAIL_MIN = 40.0


class ChargeManager:
    def __init__(self, hub: "ScooterHub") -> None:
        self.hub = hub
        self.hass = hub.hass
        self.auto_enabled = True
        self.status = "idle"                # idle | waiting | charging | finished
        self.session: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.total_kwh = 0.0
        self.total_sessions = 0
        self.model: dict[str, float] | None = None      # {"a","b","n"}
        self.tail_min: float | None = None
        self.full_level = 100.0                          # what the scooter's gauge shows when full (learned)
        self.verified_errors: list[float] = []           # real minus estimated end level, recent charges
        self.away_seen = True
        self.last_estimate: float | None = None          # kept after a session until the scooter reports a battery
        self._waiting_since: datetime | None = None
        self._power_ticks = 0
        self._plug_off_ticks = 0
        self._target_ticks = 0
        self._unsub = None

    # ---- persistence ---------------------------------------------------------------------------
    def load(self, d: dict[str, Any]) -> None:
        self.auto_enabled = d.get("auto_enabled", True)
        self.history = list(d.get("history", []))[-HISTORY_MAX:]
        self.total_kwh = float(d.get("total_kwh", sum(h.get("energy_kwh", 0) for h in self.history)))
        self.total_sessions = int(d.get("total_sessions", len(self.history)))
        self.last_estimate = d.get("last_estimate")
        saved_status = d.get("status", "idle")
        if saved_status in ("charging", "waiting") and (d.get("session") or saved_status == "waiting"):
            self.status = saved_status
            self.session = d.get("session")
            self._waiting_since = dt_util.utcnow()
        elif saved_status == "finished":
            self.status = "finished"
        self._refit()

    def dump(self) -> dict[str, Any]:
        return {
            "auto_enabled": self.auto_enabled, "history": self.history, "total_kwh": round(self.total_kwh, 3),
            "total_sessions": self.total_sessions, "status": self.status, "session": self.session,
            "last_estimate": self.last_estimate,
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
        # A session restored after a restart may be stale (the plug was switched off and on meanwhile).
        self.hass.async_create_task(self._resync_session())

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    # ---- charge model --------------------------------------------------------------------------
    def _refit(self) -> None:
        """Learn from the finished charges. A charge that ended at a level the scooter itself reported afterwards
        (`real_percent`) counts with that real end; a charge that ran to the end of the taper counts as full."""
        hist = self.history[-20:]
        fulls = [float(h["real_percent"]) for h in hist
                 if h.get("reason") == "taper" and h.get("real_percent") is not None and 80.0 <= float(h["real_percent"]) <= 100.0]
        self.full_level = round(statistics.median(fulls[-5:]), 1) if fulls else 100.0
        self.verified_errors = [float(h["error"]) for h in hist if h.get("error") is not None][-10:]
        pts: list[tuple[float, float, float]] = []   # (percent gained, 1 if it ran to full, energy kWh)
        for h in hist:
            if h.get("start_percent") is None or h.get("start_source") != "scooter":
                continue
            taper = h.get("reason") == "taper"
            if h.get("real_percent") is not None:
                end = float(h["real_percent"])
            elif taper and h.get("learn"):
                end = self.full_level
            else:
                continue
            x = end - float(h["start_percent"])
            if x >= MIN_LEARN_GAP and float(h["energy_kwh"]) >= MIN_LEARN_KWH:
                pts.append((x, 1.0 if taper else 0.0, float(h["energy_kwh"])))
        tails = [float(h["tail_min"]) for h in hist if h.get("tail_min")]
        self.tail_min = round(statistics.median(tails), 1) if tails else None
        self.model = None
        if not pts:
            return
        a, b = 0.0, statistics.median(e / x for x, _, e in pts)
        me = statistics.fmean(e for _, _, e in pts)
        if len(pts) >= 3:
            sxx = sum(x * x for x, _, _ in pts); sxt = sum(x * tt for x, tt, _ in pts); stt = sum(tt * tt for _, tt, _ in pts)
            sxy = sum(x * e for x, _, e in pts); sty = sum(tt * e for _, tt, e in pts)
            det = sxx * stt - sxt * sxt
            if abs(det) > 1e-6 * max(1.0, sxx * stt):
                b2 = (sxy * stt - sty * sxt) / det
                a2 = (sty * sxx - sxy * sxt) / det
                if b2 > 0 and 0 <= a2 <= 0.6 * me:      # physically sensible: positive slope, modest offset
                    a, b = a2, b2
        self.model = {"a": a, "b": b, "n": float(len(pts))}

    @property
    def mean_abs_error(self) -> float | None:
        e = self.verified_errors
        return round(sum(abs(x) for x in e) / len(e), 1) if e else None

    @property
    def kwh_per_percent(self) -> float | None:
        return round(self.model["b"], 5) if self.model else None

    @property
    def model_sessions(self) -> int:
        return int(self.model["n"]) if self.model else 0

    def _tail_e(self) -> float:
        m = self.model or {"a": 0.0, "b": 0.0}
        return m["a"] + m["b"] * TAIL_PCT

    def _e_to_full(self, soc: float) -> float:
        """Energy (kWh) needed from [soc] % to a full battery."""
        m = self.model
        if not m:
            return 0.0
        tail = self._tail_e()
        top = self.full_level
        if soc >= top - TAIL_PCT:
            return tail * max(0.0, top - soc) / TAIL_PCT
        return tail + m["b"] * (top - TAIL_PCT - soc)

    def _soc_after(self, start: float, energy: float) -> float:
        m = self.model
        r = max(0.0, self._e_to_full(start) - energy)
        tail = self._tail_e()
        top = self.full_level
        if r >= tail:
            return top - TAIL_PCT - (r - tail) / m["b"]
        return top - TAIL_PCT * r / tail if tail > 0 else top

    def _minutes_from(self, soc: float, peak_kw: float) -> float:
        """Minutes to charge from [soc] % to full: constant-current part at the session's power, then the tail."""
        e = self._e_to_full(soc)
        tail = self._tail_e()
        cc = max(0.0, e - tail) / max(peak_kw * 0.92, 0.03) * 60.0
        tail_part = (self.tail_min or DEFAULT_TAIL_MIN) * (min(e, tail) / tail if tail > 0 else 0.0)
        return cc + tail_part

    # ---- live values ---------------------------------------------------------------------------
    @property
    def estimate(self) -> float | None:
        """Battery % now: modelled during a charge, the final value afterwards (until the scooter reports again)."""
        s = self.session
        if self.status == "charging" and s and self.model and s.get("start_percent") is not None:
            return round(min(self.full_level, max(float(s["start_percent"]), self._soc_after(float(s["start_percent"]), s["energy"]))), 1)
        return self.last_estimate

    @property
    def remaining_min(self) -> float | None:
        s = self.session
        if self.status != "charging" or not s or not self.model or s.get("start_percent") is None:
            return None
        soc = self.estimate
        if soc is None:
            return None
        peak = max(float(s.get("peak_w", 0.0)), MIN_ON_W) / 1000.0
        top = self.full_level
        goal = float(self.target) if self.model_sessions >= STOP_MODEL_SESSIONS and self.target < top else top
        if soc >= goal:
            return 0.0
        return round(max(0.0, self._minutes_from(soc, peak) - (self._minutes_from(goal, peak) if goal < top else 0.0)), 0)

    @property
    def power_w(self) -> float | None:
        return self._float_state(self._o.get(CONF_POWER)) if self.status in ("charging", "waiting") else None

    @property
    def duration_min(self) -> float | None:
        if self.status == "charging" and self.session:
            start = dt_util.parse_datetime(self.session["start"])
            if start:
                return round((dt_util.utcnow() - start).total_seconds() / 60.0, 0)
        return None

    @property
    def last_session(self) -> dict[str, Any] | None:
        if not self.history:
            return None
        return {k: v for k, v in self.history[-1].items() if k != "curve"}

    def snapshot(self) -> dict[str, Any]:
        s = self.session
        return {
            "status": self.status, "target": self.target, "enabled": self.enabled,
            "estimate": self.estimate,
            "start_percent": s.get("start_percent") if s else None,
            "started_at": s["start"] if s else None,
            "energy_kwh": round(s["energy"], 3) if s else None,
            "power_w": self.power_w,
            "remaining_min": self.remaining_min,
            "kwh_per_percent": self.kwh_per_percent,
            "model_sessions": self.model_sessions, "total_sessions": self.total_sessions,
            "full_level": self.full_level, "estimate_error": self.mean_abs_error,
            "last": self.last_session,
        }

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

    def on_fresh_battery(self) -> None:
        self.verify_with_scooter()
        self.last_estimate = None  # the scooter itself reported a battery level again

    def verify_with_scooter(self) -> None:
        """The scooter was switched on after a charge: its own level is the truth. Compare it with the estimate that
        was stored when the charge ended, keep both, and learn from the difference."""
        if self.status == "charging" or not self.history:
            return
        rec = self.history[-1]
        if rec.get("real_percent") is not None:
            return
        real = self.hub.data.get("battery")
        odo, odo_end = self.hub.data.get("odometer"), rec.get("odo_end")
        if real is None or odo is None or odo_end is None or abs(float(odo) - float(odo_end)) > 0.3:
            return   # not comparable: it has been ridden (or we cannot tell)
        end = dt_util.parse_datetime(rec["end"])
        if end is None or dt_util.utcnow() - end > timedelta(hours=24):
            return
        real = float(real)
        if rec.get("reason") == "taper" and real < 80.0:
            return   # a "full" charge that reads this low is a gauge glitch, not data
        rec["real_percent"] = real
        rec["verified_at"] = dt_util.utcnow().isoformat()
        est = rec.get("end_percent")
        rec["error"] = round(real - float(est), 1) if est is not None else None
        _LOGGER.info("Charge verified: estimated %s %%, the scooter reports %s %%", est, real)
        self._refit()
        self.hass.bus.async_fire("openscooter_charge_verified", {"estimated": est, "real": real, "error": rec["error"]})
        self.hub.notify()
        self.hub.save_soon()

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
        self._plug_off_ticks = self._target_ticks = 0
        self.hub.notify()
        self.hub.save_soon()
        _LOGGER.info("Smart charging: plug on (%s)", reason)

    async def _real_start(self, now: datetime, power: float) -> tuple[datetime, float, float | None]:
        """When did the charger really start delivering power?

        Looks into the recorded history of the plug's power sensor (and its energy counter) and returns
        (start time, energy already delivered in kWh, counter value at the start or None). Falls back to
        "now" when there is no history, so a missing recorder never breaks charging.
        """
        power_id, energy_id = self._o.get(CONF_POWER), self._o.get(CONF_ENERGY)
        try:
            if not power_id or "recorder" not in self.hass.config.components:
                raise LookupError("no recorder")
            from homeassistant.components.recorder import get_instance, history

            begin = now - BACKFILL
            ids = [power_id] + ([energy_id] if energy_id else []) + ([self.plug] if self.plug else [])
            states = await get_instance(self.hass).async_add_executor_job(
                lambda: history.get_significant_states(self.hass, begin, now, ids, None, True, False, False, True))
        except Exception as err:  # noqa: BLE001 - history is a nicety, never a requirement
            _LOGGER.warning("Could not read the plug history for the charge start: %s", err)
            return now, 0.0, None

        def series(entity_id):
            out = []
            for st in states.get(entity_id, []) if entity_id else []:
                try:
                    out.append((st.last_updated, float(st.state)))
                except (TypeError, ValueError):
                    out.append((st.last_updated, None))
            return sorted(out, key=lambda x: x[0])

        pts = series(power_id)
        if not pts:
            return now, 0.0, None
        # Walk back from now over the numeric readings: the run ends at the last reading below MIN_ON_W.
        # Unavailable/unknown readings (e.g. while Home Assistant restarted) neither start nor break a run.
        start = None
        for i in range(len(pts) - 1, -1, -1):
            ts, w = pts[i]
            if w is None:
                continue
            if w < MIN_ON_W:
                break
            start = ts
        if start is None:
            return now, 0.0, None
        # The plug cannot have delivered power while it was switched off: start after its last "off".
        if self.plug:
            sw = sorted(((st.last_updated, st.state) for st in states.get(self.plug, [])), key=lambda x: x[0])
            last_off = max((ts for ts, s_ in sw if s_ == "off"), default=None)
            if last_off is not None and last_off > start:
                on_after = [ts for ts, s_ in sw if s_ == "on" and ts > last_off]
                start = on_after[0] if on_after else start
        start = min(start, now)

        e_start = None
        energy = 0.0
        eser = series(energy_id)
        if eser:
            before = [v for ts, v in eser if ts <= start and v is not None]
            e_start = before[-1] if before else next((v for _, v in eser if v is not None), None)
        run = [(ts, w) for ts, w in pts if ts >= start and w is not None]
        for (t0, w0), (t1, w1) in zip(run, run[1:] + [(now, run[-1][1] if run else 0.0)]):
            energy += (w0 + w1) / 2.0 * max(0.0, (t1 - t0).total_seconds()) / 3600.0 / 1000.0
        return start, energy, e_start

    async def _resync_session(self) -> None:
        """Move the start of the running session to when the charger really started (history of the plug)."""
        s = self.session
        if self.status != "charging" or s is None:
            return
        now = dt_util.utcnow()
        power = self._float_state(self._o.get(CONF_POWER)) or s.get("last_power") or 0.0
        start, energy0, e_hist = await self._real_start(now, power)
        old = dt_util.parse_datetime(s["start"]) or now
        if self.session is not s or abs((start - old).total_seconds()) <= 120:
            return
        _LOGGER.info("Charge start corrected from %s to %s (plug history)", old, start)
        s.update({"start": start.isoformat(), "energy": energy0, "e_start": e_hist if e_hist is not None else s.get("e_start"),
                  "last_ts": now.isoformat(), "low_since": None, "peak_w": max(float(s.get("peak_w", 0)), power),
                  "curve": [[0.0, round(energy0, 4), round(power, 1)]], "curve_ts": now.isoformat()})
        self.hub.notify()
        self.hub.save_soon()

    async def _start_session(self, now: datetime, power: float) -> None:
        start, energy0, e_hist = await self._real_start(now, power)
        e = e_hist if e_hist is not None else self._float_state(self._o.get(CONF_ENERGY))
        battery = self.hub.data.get("battery")
        seen = self.hub.battery_ts
        age_h = (start - seen).total_seconds() / 3600.0 if seen else None
        if battery is not None and (age_h is None or age_h > STALE_BATTERY_H):
            _LOGGER.info("Battery level %s %% is too old to be the start of this charge; it is inferred afterwards", battery)
            battery = None   # unknown beats wrong: the model infers it from the energy when the charge is done
        self.session = {
            "start": start.isoformat(), "start_percent": battery,
            "start_source": "scooter" if battery is not None else None,
            "start_percent_age_min": round(age_h * 60) if age_h is not None and battery is not None else None,
            "energy": energy0, "last_ts": now.isoformat(), "last_power": power, "peak_w": power,
            "e_start": e, "low_since": None, "curve": [[0.0, round(energy0, 4), round(power, 1)]], "curve_ts": now.isoformat(),
        }
        self.status = "charging"
        self._power_ticks = self._plug_off_ticks = self._target_ticks = 0
        self.last_estimate = None
        self.hass.bus.async_fire(EVENT_CHARGE_STARTED, {"start_percent": battery, "started_at": start.isoformat()})
        self.hub.notify()
        self.hub.save_soon()
        _LOGGER.info("Charge session started %s ago (%.3f kWh already)", now - start, energy0)

    async def _power_curve(self, start: datetime, end: datetime) -> list[list[float]] | None:
        """The real power curve of the session from the plug's recorded history, one point per minute:
        [minutes since start, kWh so far, watts]. None when there is no history."""
        power_id = self._o.get(CONF_POWER)
        try:
            if not power_id or "recorder" not in self.hass.config.components:
                return None
            from homeassistant.components.recorder import get_instance, history

            states = await get_instance(self.hass).async_add_executor_job(
                lambda: history.get_significant_states(self.hass, start, end, [power_id], None, True, False, False, True))
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("No history for the power curve: %s", err)
            return None
        pts: list[tuple[datetime, float]] = []
        for st in states.get(power_id, []):
            try:
                pts.append((st.last_updated, float(st.state)))
            except (TypeError, ValueError):
                continue
        pts.sort(key=lambda x: x[0])
        if len(pts) < 2:
            return None
        curve: list[list[float]] = []
        energy = 0.0
        total_min = int((end - start).total_seconds() // 60)
        idx, last_w, last_t = 0, 0.0, start
        for m in range(0, min(total_min, CURVE_MAX * 4) + 1):
            t_m = start + timedelta(minutes=m)
            while idx < len(pts) and pts[idx][0] <= t_m:
                ts, w = pts[idx]
                ts = max(ts, last_t)
                energy += last_w * (ts - last_t).total_seconds() / 3600.0 / 1000.0
                last_t, last_w = ts, w
                idx += 1
            energy += last_w * (t_m - last_t).total_seconds() / 3600.0 / 1000.0
            last_t = t_m
            curve.append([float(m), round(energy, 4), round(last_w, 1)])
        return curve

    async def async_end(self, reason: str) -> None:
        """Stop now: plug off, store the session, refit the model."""
        if self.plug and reason != "plug_off":
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": self.plug}, blocking=False)
        s = self.session
        if s is None:
            self.status = "idle"
            self.hub.notify()
            return
        now = dt_util.utcnow()
        started = dt_util.parse_datetime(s["start"]) or now
        energy = round(s["energy"], 3)
        minutes = max(0.0, (now - started).total_seconds() / 60.0)
        start_pct = s.get("start_percent")
        source = s.get("start_source")
        curve = s.get("curve", [])
        full = await self._power_curve(started, now)
        if full:
            curve = full
        peak = max([float(s.get("peak_w", 0.0))] + [c[2] for c in curve])

        # Tail length: time since the power last was near its peak.
        tail_min = None
        if reason == "taper" and curve and peak > 0:
            near = [c[0] for c in curve if c[2] >= 0.85 * peak]
            if near:
                tail_min = round(max(0.0, minutes - max(near)), 1)

        end_pct: float | None = None
        if reason == "taper":
            end_pct = self.full_level
        elif reason == "target":
            end_pct = float(self.target)
        elif start_pct is not None and self.model:
            end_pct = round(min(self.full_level, self._soc_after(float(start_pct), energy)), 1)

        learn = False
        if reason == "taper" and start_pct is not None and source == "scooter":
            gap = self.full_level - float(start_pct)
            learn = gap >= MIN_LEARN_GAP and energy >= MIN_LEARN_KWH and minutes >= MIN_LEARN_MIN
            if learn and self.model:                       # reject interrupted / odd sessions
                pred = self.model["a"] + self.model["b"] * gap
                if pred > 0 and abs(energy - pred) > 0.6 * pred:
                    learn = False
        elif reason == "taper" and start_pct is None and self.model and energy >= MIN_LEARN_KWH:
            gap = (energy - self.model["a"]) / self.model["b"]          # infer where it started
            start_pct = round(max(0.0, min(100.0, self.full_level - gap)), 1)
            source = "inferred"

        rec = {
            "id": s["start"], "start": s["start"], "end": now.isoformat(), "start_percent": start_pct,
            "start_source": source, "end_percent": end_pct, "energy_kwh": energy, "duration_min": round(minutes, 1),
            "peak_w": round(peak, 1), "avg_w": round(energy * 1000.0 / (minutes / 60.0), 1) if minutes > 1 else None,
            "reason": reason, "learn": learn, "tail_min": tail_min, "end_source": "estimate",
            "odo_end": self.hub.data.get("odometer"), "real_percent": None, "error": None, "verified_at": None, "curve": curve[-CURVE_MAX * 4:],
        }
        self.history = (self.history + [rec])[-HISTORY_MAX:]
        for old in self.history[:-30]:
            old.pop("curve", None)  # curves are kept for the last 30 sessions only
        self.total_kwh += energy
        self.total_sessions += 1
        self._refit()
        self.last_estimate = end_pct
        self.session = None
        self.status = "finished"
        self.hass.bus.async_fire(EVENT_CHARGE_FINISHED, {k: v for k, v in rec.items() if k != "curve"})
        self.hub.notify()
        await self.hub.store.async_save(self.hub._dump())
        _LOGGER.info("Smart charging finished (%s): %.3f kWh in %.0f min", reason, energy, minutes)

    # ---- the 15 s evaluation -------------------------------------------------------------------
    async def _tick(self, _now=None) -> None:
        self.hub.check_connection()
        power = self._float_state(self._o.get(CONF_POWER))
        now = dt_util.utcnow()

        # A charge started by hand (plug switched on outside the integration) is picked up too.
        if self.status in ("idle", "finished") and self.enabled:
            if power is not None and power >= MIN_ON_W:
                self._power_ticks += 1
                if self._power_ticks >= 2:
                    await self._start_session(now, power)
                    return
            else:
                self._power_ticks = 0

        if self.status == "waiting":
            if power is not None and power >= MIN_ON_W:
                await self._start_session(now, power)
            elif self._waiting_since and now - self._waiting_since > WAIT_FOR_POWER:
                if self.plug:
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": self.plug}, blocking=False)
                self.status = "idle"
                self.hub.notify()
            return

        if self.status != "charging" or self.session is None:
            return
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 40 == 0:
            await self._resync_session()
        s = self.session
        started = dt_util.parse_datetime(s["start"]) or now

        # The plug was switched off by someone else: end without teaching the model.
        plug_state = self.hass.states.get(self.plug) if self.plug else None
        if plug_state is not None and plug_state.state == "off":
            self._plug_off_ticks += 1
            if self._plug_off_ticks >= 3:
                await self.async_end("plug_off")
                return
        else:
            self._plug_off_ticks = 0

        # Energy: the plug's own counter when there is one, otherwise integrate its power.
        last_ts = dt_util.parse_datetime(s["last_ts"]) or now
        hours = min(MAX_INTEGRATE_H, max(0.0, (now - last_ts).total_seconds() / 3600.0))
        e_now = self._float_state(self._o.get(CONF_ENERGY))
        if e_now is not None and s.get("e_start") is not None and e_now >= s["e_start"]:
            s["energy"] = e_now - s["e_start"]
        elif power is not None:
            s["energy"] += (s.get("last_power", power) + power) / 2.0 * hours / 1000.0
        if power is not None:
            s["last_power"] = power
            s["peak_w"] = max(float(s.get("peak_w", 0.0)), power)
        s["last_ts"] = now.isoformat()

        # Coarse power curve (kept with the session for later analysis).
        c_ts = dt_util.parse_datetime(s.get("curve_ts", s["start"])) or started
        if (now - c_ts).total_seconds() >= CURVE_STEP_S and power is not None and len(s["curve"]) < CURVE_MAX:
            s["curve"].append([round((now - started).total_seconds() / 60.0, 1), round(s["energy"], 4), round(power, 1)])
            s["curve_ts"] = now.isoformat()

        # "Done" = power under the larger of your setting and 12 % of this charge's peak (a 100 W brick: about 12 W, which is
        # where its green light comes on); the brick then only idles or trickles, so waiting longer adds almost nothing.
        off_w = min(END_MAX_W, max(float(self._o.get(CONF_OFF_W, DEFAULT_OFF_W)), END_FRACTION * float(s.get("peak_w", 0.0))))
        off_min = float(self._o.get(CONF_OFF_MIN, DEFAULT_OFF_MIN))

        # Finished: power under the threshold for long enough (an unavailable sensor never counts as "low").
        if power is not None:
            if power < off_w and now - started > timedelta(minutes=5):
                if s["low_since"] is None:
                    s["low_since"] = now.isoformat()
                low = dt_util.parse_datetime(s["low_since"]) or now
                if now - low >= timedelta(minutes=off_min):
                    await self.async_end("taper")
                    return
            else:
                s["low_since"] = None

        # "Charge up to X %": only with a trained model, and only after two checks in a row.
        est = self.estimate
        if est is not None and self.model_sessions >= STOP_MODEL_SESSIONS and self.target < self.full_level:
            if est >= self.target:
                self._target_ticks += 1
                if self._target_ticks >= 2:
                    await self.async_end("target")
                    return
            else:
                self._target_ticks = 0

        if now - started > MAX_SESSION:
            await self.async_end("timeout")
            return
        self.hub.notify()
        self.hub.save_soon()
