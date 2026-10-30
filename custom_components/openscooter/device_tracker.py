from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity

from .const import DOMAIN
from .entity import ScooterEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([ScooterTracker(hass.data[DOMAIN][entry.entry_id])])


class ScooterTracker(ScooterEntity, TrackerEntity):
    """Where the scooter is (its last position, which is where it is parked while it charges)."""

    def __init__(self, hub) -> None:
        super().__init__(hub, "position", "Position")

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self):
        return self.hub.data.get("lat")

    @property
    def longitude(self):
        return self.hub.data.get("lon")

    @property
    def location_accuracy(self):
        return int(self.hub.data.get("accuracy") or 0)
