"""Sensor platform for SMA Inverter (Modbus)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmaConfigEntry
from .coordinator import SmaCoordinator
from .entity import SmaEntity
from .sma_modbus import (
    Condition,
    Derating,
    FallbackMode,
    LimitSource,
    OperatingMode,
    SmaData,
)


@dataclass(frozen=True, kw_only=True)
class SmaSensorDescription(SensorEntityDescription):
    """A sensor with a value extractor."""

    value_fn: Callable[[SmaData], float | int | str | None]


SENSORS: tuple[SmaSensorDescription, ...] = (
    # -- production ---------------------------------------------------------
    # THREE POWERS, easily confused. pv_power (35469) is what the panels make,
    # including whatever goes to the battery; ac_power (30775) is what leaves
    # the inverter. Their DIFFERENCE goes to the battery, which is why a low
    # ac_power is not evidence of curtailment on its own.
    SmaSensorDescription(
        key="pv_power",
        translation_key="pv_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: data.pv_power,
    ),
    SmaSensorDescription(
        key="ac_power",
        translation_key="ac_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: data.ac_power,
    ),
    SmaSensorDescription(
        key="total_yield",
        translation_key="total_yield",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda data: data.total_yield,
    ),
    SmaSensorDescription(
        key="apparent_power",
        translation_key="apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        value_fn=lambda data: data.apparent_power,
    ),
    SmaSensorDescription(
        key="reactive_power",
        translation_key="reactive_power",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        value_fn=lambda data: data.reactive_power,
    ),
    SmaSensorDescription(
        key="ac_voltage",
        translation_key="ac_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda data: data.ac_voltage,
    ),
    SmaSensorDescription(
        key="ac_current",
        translation_key="ac_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        value_fn=lambda data: data.ac_current,
    ),
    SmaSensorDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.grid_frequency,
    ),
    # -- limit control ------------------------------------------------------
    # The mode decides whether a setpoint write does anything at all, so this
    # is the first sensor to look at when a limit is not taking effect.
    SmaSensorDescription(
        key="operating_mode",
        translation_key="operating_mode",
        device_class=SensorDeviceClass.ENUM,
        options=[mode.name.lower() for mode in OperatingMode],
        value_fn=lambda data: data.operating_mode.name.lower(),
    ),
    # 35547: says by WHOM the limit is imposed, where the limit registers only
    # say THAT something is limiting.
    SmaSensorDescription(
        key="limit_source",
        translation_key="limit_source",
        device_class=SensorDeviceClass.ENUM,
        options=[source.name.lower() for source in LimitSource],
        value_fn=lambda data: data.limit_source.name.lower(),
    ),
    # 31405: the limit the inverter APPLIES, whatever imposed it. Says whether
    # a written setpoint is honoured, not merely accepted.
    SmaSensorDescription(
        key="power_limit_applied",
        translation_key="power_limit_applied",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: data.power_limit_applied,
    ),
    SmaSensorDescription(
        key="fallback_mode",
        translation_key="fallback_mode",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[mode.name.lower() for mode in FallbackMode],
        value_fn=lambda data: data.fallback_mode.name.lower(),
    ),
    SmaSensorDescription(
        key="fallback_timeout",
        translation_key="fallback_timeout",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        value_fn=lambda data: data.fallback_timeout,
    ),
    # -- diagnostics: read once at setup, refreshed by the button -----------
    SmaSensorDescription(
        key="condition",
        translation_key="condition",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[state.name.lower() for state in Condition],
        value_fn=lambda data: data.condition.name.lower(),
    ),
    SmaSensorDescription(
        key="derating",
        translation_key="derating",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[state.name.lower() for state in Derating],
        value_fn=lambda data: data.derating.name.lower(),
    ),
    SmaSensorDescription(
        key="message",
        translation_key="message",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.message,
    ),
    # 31547 against ac_power quantifies curtailment DIRECTLY, instead of
    # inferring it by mirroring a setpoint.
    SmaSensorDescription(
        key="available_power",
        translation_key="available_power",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: data.available_power,
    ),
    # 41203 "Nominal PV system power". MEASURED AS 0 W on a Sunny Boy Smart
    # Energy 5.0, so despite reading like the obvious PMAX reference it is
    # useless as one here - do not tell anyone to check their PMAX against it.
    # 30233 (WMax) is the register that actually answers, which is why that one
    # is enabled by default and this one is not.
    SmaSensorDescription(
        key="nominal_pv_power",
        translation_key="nominal_pv_power",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.nominal_pv_power,
    ),
    SmaSensorDescription(
        key="nominal_power_ok",
        translation_key="nominal_power_ok",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.nominal_power_ok,
    ),
    # 30233 WMax: the reference PMAX must match, and the one that actually
    # reports a value on this hardware. Enabled by default for that reason -
    # a wrong PMAX silently rescales every limit the integration writes.
    SmaSensorDescription(
        key="wmax",
        translation_key="wmax",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: data.wmax,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(SmaSensor(coordinator, desc) for desc in SENSORS)


class SmaSensor(SmaEntity, SensorEntity):
    """An SMA measurement or diagnostic sensor."""

    entity_description: SmaSensorDescription

    def __init__(
        self, coordinator: SmaCoordinator, description: SmaSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | str | None:
        """The sensor's current value.

        ``None`` propagates straight through as "unknown". That is deliberate:
        the device library returns ``None`` for a register that answered with a
        NaN sentinel, and showing "unknown" is the honest rendering - a
        fabricated 0 would poison history and statistics permanently.
        """
        return self.entity_description.value_fn(self.coordinator.data)
