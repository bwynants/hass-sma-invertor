"""Number platform: the requested power limit, in watts."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfPower
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
    """Set up the number."""
    async_add_entities([SmaPowerLimitNumber(entry.runtime_data)])


class SmaPowerLimitNumber(SmaEntity, NumberEntity, RestoreEntity):
    """The requested active power limit in watts.

    IN WATTS, not percent, because watts is what a person wants to set. The
    conversion to whole percent of PMAX happens once, in the coordinator, at
    the moment of the write - so there is exactly one place where the device
    maximum matters.

    At PMAX this means "no limit": the coordinator then stops refreshing the
    setpoint and lets the inverter's fallback lapse, rather than writing 100%
    forever.
    """

    _attr_translation_key = "power_limit"
    _attr_native_min_value = 0
    _attr_native_step = 50
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: SmaCoordinator) -> None:
        """Initialize the number."""
        super().__init__(coordinator, "power_limit")

    @property
    def available(self) -> bool:
        """Always available, so a limit can be set before the link is up.

        The value is Home Assistant's intent, not a reading from the inverter,
        so it stays settable during an outage - the coordinator re-asserts it
        when the link returns.
        """
        return True

    @property
    def native_max_value(self) -> float:
        """The configured device maximum."""
        return self.coordinator.pmax

    @property
    def native_value(self) -> float:
        """The requested limit in watts."""
        return self.coordinator.requested_watt

    async def async_set_native_value(self, value: float) -> None:
        """Set the requested limit."""
        await self.coordinator.async_set_requested_watt(value)

    async def async_added_to_hass(self) -> None:
        """Restore the requested limit into the coordinator.

        NEVER restore into an unknown state: a missing or unparseable value
        would render as 0 W, which is full curtailment. PMAX - no limit - is the
        only safe default, because getting it wrong in that direction costs
        nothing but a moment of uncurtailed production.
        """
        await super().async_added_to_hass()
        restored = self.coordinator.pmax
        if (last := await self.async_get_last_state()) is not None:
            try:
                restored = float(last.state)
            except (TypeError, ValueError):
                restored = self.coordinator.pmax
        self.coordinator.requested_watt = restored
        # Debounced, so this and the switch's restore settle into one correct
        # write whatever order the platforms happen to set up in.
        self.coordinator.schedule_setpoint_write()
