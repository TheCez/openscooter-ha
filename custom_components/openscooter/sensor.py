from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTime, UnitOfLength, UnitOfSpeed, UnitOfTemperature, UnitOfElectricPotential
from homeassistant.helpers.entity import EntityCategory

from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import ScooterEntity
from .scooter import ScooterHub


@dataclass(frozen=True, kw_only=True)
class Desc(SensorEntityDescription):
    value: Callable[[ScooterHub], Any]


def _d(hub: ScooterHub, k: str):
    return hub.data.get(k)


SENSORS: tuple[Desc, ...] = (
    Desc(key="battery", name="Battery", device_class=SensorDeviceClass.BATTERY, native_unit_of_measurement=PERCENTAGE,
         state_class=SensorStateClass.MEASUREMENT, value=lambda h: _d(h, "battery")),
    Desc(key="speed", name="Speed", device_class=SensorDeviceClass.SPEED, native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
         state_class=SensorStateClass.MEASUREMENT, value=lambda h: _d(h, "speed")),
    Desc(key="odometer", name="Odometer", device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS,
         state_class=SensorStateClass.TOTAL_INCREASING, value=lambda h: _d(h, "odometer")),
    Desc(key="trip", name="Trip distance", device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS,
         state_class=SensorStateClass.MEASUREMENT, value=lambda h: _d(h, "trip_km")),
    Desc(key="mode", name="Drive mode", value=lambda h: _d(h, "mode")),
    Desc(key="voltage", name="Battery voltage", device_class=SensorDeviceClass.VOLTAGE, native_unit_of_measurement=UnitOfElectricPotential.VOLT,
         state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, value=lambda h: _d(h, "voltage")),
    Desc(key="motor_temp", name="Motor temperature", device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS,
         state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, value=lambda h: _d(h, "motor_temp")),
    Desc(key="scooter_temp", name="Scooter temperature", device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS,
         state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, value=lambda h: _d(h, "scooter_temp")),
    Desc(key="last_seen", name="Last seen", device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC,
         value=lambda h: h.last_seen),
    Desc(key="charge_status", name="Charge status", value=lambda h: h.charge.status),
    Desc(key="battery_estimate", name="Battery estimate", device_class=SensorDeviceClass.BATTERY,
         native_unit_of_measurement=PERCENTAGE, value=lambda h: h.charge.estimate),
    Desc(key="kwh_per_percent", name="Learned energy per 1 %", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
         suggested_display_precision=4, entity_category=EntityCategory.DIAGNOSTIC, value=lambda h: h.charge.kwh_per_percent),
    Desc(key="last_charge_kwh", name="Last charge energy", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
         suggested_display_precision=2, value=lambda h: (h.charge.last_session or {}).get("energy_kwh")),
    Desc(key="session_kwh", name="Current charge energy", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
         suggested_display_precision=3, value=lambda h: round(h.charge.session["energy"], 3) if h.charge.session else None),
    Desc(key="charge_started", name="Charging started", device_class=SensorDeviceClass.TIMESTAMP,
         value=lambda h: dt_util.parse_datetime(h.charge.session["start"]) if h.charge.session else None),
    Desc(key="charge_start_percent", name="Charge start level", device_class=SensorDeviceClass.BATTERY,
         native_unit_of_measurement=PERCENTAGE,
         value=lambda h: h.charge.session.get("start_percent") if h.charge.session else None),
    Desc(key="charge_power", name="Charge power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
         suggested_display_precision=0, value=lambda h: h.charge.power_w),
    Desc(key="charge_remaining", name="Charge time remaining", device_class=SensorDeviceClass.DURATION,
         native_unit_of_measurement=UnitOfTime.MINUTES, value=lambda h: h.charge.remaining_min),
    Desc(key="charge_duration", name="Charge duration", device_class=SensorDeviceClass.DURATION,
         native_unit_of_measurement=UnitOfTime.MINUTES, value=lambda h: h.charge.duration_min),
    Desc(key="charge_sessions", name="Charge sessions", state_class=SensorStateClass.TOTAL_INCREASING,
         entity_category=EntityCategory.DIAGNOSTIC, value=lambda h: h.charge.total_sessions),
    Desc(key="charge_total_kwh", name="Total charge energy", device_class=SensorDeviceClass.ENERGY,
         native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, state_class=SensorStateClass.TOTAL_INCREASING,
         suggested_display_precision=2, value=lambda h: round(h.charge.total_kwh, 3)),
    Desc(key="range_estimate", name="Estimated range", device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS,
         state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1, value=lambda h: _d(h, "range_km")),
    Desc(key="range_full", name="Range on a full battery", device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS,
         suggested_display_precision=1, value=lambda h: _d(h, "range_full_km")),
    Desc(key="range_error", name="Range model error", native_unit_of_measurement=PERCENTAGE, suggested_display_precision=1,
         entity_category=EntityCategory.DIAGNOSTIC, value=lambda h: _d(h, "range_error_pct")),
    Desc(key="range_samples", name="Range model observations", state_class=SensorStateClass.TOTAL_INCREASING,
         entity_category=EntityCategory.DIAGNOSTIC, value=lambda h: _d(h, "range_samples")),
    Desc(key="ride_count", name="Rides", state_class=SensorStateClass.TOTAL_INCREASING, value=lambda h: len(h.rides)),
    Desc(key="ride_distance_total", name="Distance ridden (synced rides)", device_class=SensorDeviceClass.DISTANCE,
         native_unit_of_measurement=UnitOfLength.KILOMETERS, state_class=SensorStateClass.TOTAL_INCREASING, suggested_display_precision=1,
         value=lambda h: round(sum(float(r.get("dist") or 0) for r in h.rides.values()) / 1000.0, 2)),
    Desc(key="last_ride", name="Last ride", device_class=SensorDeviceClass.TIMESTAMP, value=lambda h: _last_ride_end(h)),
    Desc(key="data_segments", name="Stored range observations", entity_category=EntityCategory.DIAGNOSTIC, value=lambda h: len(h.segments)),
    Desc(key="data_synced", name="Data last synced", device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC,
         value=lambda h: h.data_synced),
)


def _last_ride(h: ScooterHub):
    if not h.rides:
        return None
    return max(h.rides.values(), key=lambda r: r.get("end") or 0)


def _last_ride_end(h: ScooterHub):
    r = _last_ride(h)
    return dt_util.utc_from_timestamp(float(r["end"]) / 1000.0) if r and r.get("end") else None


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    hub: ScooterHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ScooterSensor(hub, d) for d in SENSORS)


class ScooterSensor(ScooterEntity, SensorEntity):
    entity_description: Desc

    def __init__(self, hub: ScooterHub, desc: Desc) -> None:
        super().__init__(hub, desc.key, desc.name)
        self.entity_description = desc

    @property
    def native_value(self):
        return self.entity_description.value(self.hub)

    @property
    def extra_state_attributes(self):
        if self.entity_description.key == "charge_status":
            c = self.hub.charge
            return {"target_percent": c.target, "smart_charging": c.enabled, "last_session": c.last_session,
                    "recent_sessions": [{k: v for k, v in r.items() if k != "curve"} for r in c.history[-10:]]}
        if self.entity_description.key == "range_estimate":
            d = self.hub.data
            return {"low_km": d.get("range_low_km"), "high_km": d.get("range_high_km"), "mode": d.get("mode"),
                    "model_observations": d.get("range_samples"), "model_error_percent": d.get("range_error_pct")}
        if self.entity_description.key == "last_ride":
            r = _last_ride(self.hub)
            if r:
                return {"distance_km": round(float(r.get("dist") or 0) / 1000.0, 2), "moving_s": r.get("moving"),
                        "avg_kmh": r.get("avg"), "max_kmh": r.get("max"), "battery_start": r.get("b0"), "battery_end": r.get("b1")}
        return None
