"""Switch platform: the heartbeat.

The heartbeat only has a purpose with an ARMED fallback (41193 = 2507): only
then does the inverter drop the limit when the writes stop, so only then does
the limit have to be refreshed. With 2506 the inverter keeps the last value by
itself and a heartbeat is pure bus traffic - which is why the fallback button
moves this flag along with the mode it writes.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SmaConfigEntry
from .coordinator import SmaCoordinator
from .entity import SmaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    async_add_entities([SmaHeartbeatSwitch(entry.runtime_data)])


class SmaHeartbeatSwitch(SmaEntity, SwitchEntity, RestoreEntity):
    """On: keep refreshing the setpoint so an armed fallback cannot lapse."""

    _attr_translation_key = "heartbeat"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SmaCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "heartbeat")

    @property
    def available(self) -> bool:
        """Always available: it is our own policy flag, not a device reading."""
        return True

    @property
    def is_on(self) -> bool:
        """Whether the heartbeat is armed."""
        return self.coordinator.heartbeat

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Arm the heartbeat."""
        await self.coordinator.async_set_heartbeat(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disarm the heartbeat."""
        await self.coordinator.async_set_heartbeat(False)

    async def async_added_to_hass(self) -> None:
        """Restore the flag into the coordinator.

        Safe to restore verbatim, unlike the slider: the heartbeat only ever
        REFRESHES a limit that the slider already asks for, so a wrong value
        here cannot introduce a curtailment nobody requested. The mode gate in
        the coordinator stops it from ever touching a flash register.
        """
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.heartbeat = bool(last and last.state == "on")
