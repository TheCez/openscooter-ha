from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import ScooterEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([SmartCharging(hass.data[DOMAIN][entry.entry_id])])


class SmartCharging(ScooterEntity, SwitchEntity):
    """Master switch for automatic charging (the plug itself is chosen in the integration options)."""
    _attr_icon = "mdi:ev-plug-type2"

    def __init__(self, hub) -> None:
        super().__init__(hub, "smart_charging", "Smart charging")

    @property
    def is_on(self) -> bool:
        return self.hub.charge.auto_enabled

    async def async_turn_on(self, **kwargs) -> None:
        self.hub.charge.auto_enabled = True
        self.hub.save_soon()
        self.hub.notify()

    async def async_turn_off(self, **kwargs) -> None:
        self.hub.charge.auto_enabled = False
        self.hub.save_soon()
        self.hub.notify()
