"""Base entity for the SMA Inverter integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, DOMAIN
from .coordinator import SmaCoordinator


class SmaEntity(CoordinatorEntity[SmaCoordinator]):
    """Shared device info and coordinator wiring."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmaCoordinator, key: str) -> None:
        """Bind the entity to its coordinator and datapoint key."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}-{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="SMA",
            # Kept short deliberately: with `_attr_has_entity_name` the device
            # name prefixes every entity id, so this is what makes them come
            # out as `sensor.sma_pv_power` rather than something longer.
            name="SMA",
            model="Sunny Boy Smart Energy",
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )
