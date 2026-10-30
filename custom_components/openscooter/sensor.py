from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfLength, UnitOfSpeed, UnitOfTemperature, UnitOfElectricPotential
from homeassistant.helpers.entity import EntityCategory

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
)


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
            return {"target_percent": c.target, "smart_charging": c.enabled, "last_session": c.last_session}
        return None
