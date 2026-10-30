from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .const import DOMAIN
from .entity import ScooterEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    hub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        Flag(hub, "connected", "Connected", BinarySensorDeviceClass.CONNECTIVITY, lambda h: h.connected),
        # LOCK device class: on = unlocked (Home Assistant convention)
        Flag(hub, "unlocked", "Unlocked", BinarySensorDeviceClass.LOCK,
             lambda h: None if h.data.get("locked") is None else not h.data["locked"]),
        Flag(hub, "headlight", "Headlight", BinarySensorDeviceClass.LIGHT, lambda h: h.data.get("headlight")),
        Flag(hub, "charging", "Charging", BinarySensorDeviceClass.BATTERY_CHARGING, lambda h: h.charge.status == "charging"),
    ])


class Flag(ScooterEntity, BinarySensorEntity):
    def __init__(self, hub, key, name, device_class, fn) -> None:
        super().__init__(hub, key, name)
        self._attr_device_class = device_class
        self._fn = fn

    @property
    def is_on(self):
        return self._fn(self.hub)
