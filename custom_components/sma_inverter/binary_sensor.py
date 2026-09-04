"""Binary sensor platform: the problem detectors.

Each answers a question the raw registers cannot answer on their own. The one
principle that carries across all of them: a detector that cannot SEE reports
UNAVAILABLE, never "off". "I cannot tell" and "it is fine" are different
answers, and collapsing them is how a broken readback passes for a healthy
inverter.

WHAT IS DELIBERATELY NOT DETECTED HERE. Two limit-comparison detectors used to
live in this file - "we asked for a limit and the inverter allows more", and
"something is limiting while we ask for nothing". Both were driven by the 41255
setpoint readback, and register, sensor and detectors have all been removed on
request. The consequence is worth stating plainly: a setpoint write that the
inverter accepts and then silently ignores - the classic symptom of mode 1079
having been set without the required inverter restart - now raises no alarm of
any kind. `sensor.*_limit_source` (35547) still names whatever is imposing the
binding limit, and is the closest remaining signal; it is a sensor to be read,
not an alarm that fires.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SmaConfigEntry
from .const import PROBLEM_DELAY_ON
from .coordinator import SmaCoordinator
from .entity import SmaEntity


@dataclass(frozen=True, kw_only=True)
class SmaProblemDescription(BinarySensorEntityDescription):
    """A problem detector reading the coordinator."""

    is_on_fn: Callable[[SmaCoordinator], bool]
    # Whether the detector can say anything right now. See the module docstring.
    available_fn: Callable[[SmaCoordinator], bool] = lambda _: True
    # How long the condition must hold before it is reported. Zero reports
    # immediately; anything racing a register readback needs a delay, because a
    # value that has not caught up with a fresh write looks like a failure.
    delay_on: timedelta = PROBLEM_DELAY_ON


PROBLEMS: tuple[SmaProblemDescription, ...] = (
    # When 40210 answers with a sentinel, every mode-dependent decision is
    # blind - including the coordinator's refusal to write outside mode 1079.
    # Worth its own sensor: "I cannot see" is a finding in itself.
    SmaProblemDescription(
        key="readback_unusable",
        translation_key="readback_unusable",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda c: c.data is not None and not c.data.readback_usable,
    ),
    # With fallback mode 2506 the inverter keeps a written limit after Home
    # Assistant stops, so releasing it takes an explicit 100% write. Worth
    # knowing, because it changes what "stop curtailing" means.
    SmaProblemDescription(
        key="fallback_not_armed",
        translation_key="fallback_not_armed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Reported at once: a configuration fact read straight from 41193, not
        # a race between a write and a readback.
        delay_on=timedelta(0),
        is_on_fn=lambda c: c.data is not None and not c.data.fallback_mode.is_armed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [SmaProblemSensor(coordinator, desc) for desc in PROBLEMS]
        + [SmaLinkProblem(coordinator)]
    )


class SmaProblemSensor(SmaEntity, BinarySensorEntity):
    """A problem detector derived from the inverter's state."""

    entity_description: SmaProblemDescription

    def __init__(
        self, coordinator: SmaCoordinator, description: SmaProblemDescription
    ) -> None:
        """Initialize the problem sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        # When the underlying condition first became true, or None while it is
        # false. This is what implements `delay_on` - see `is_on`.
        self._true_since: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Start tracking the condition from the current poll."""
        await super().async_added_to_hass()
        self._track()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-evaluate the condition on every poll, then write state."""
        self._track()
        super()._handle_coordinator_update()

    @callback
    def _track(self) -> None:
        """Note when the raw condition started, or clear it when it stops.

        A condition that flickers off resets the clock, so only a SUSTAINED
        problem is ever reported.
        """
        if self.entity_description.is_on_fn(self.coordinator):
            if self._true_since is None:
                self._true_since = dt_util.utcnow()
        else:
            self._true_since = None

    @property
    def available(self) -> bool:
        """Whether the detector can currently say anything."""
        return super().available and self.entity_description.available_fn(
            self.coordinator
        )

    @property
    def is_on(self) -> bool:
        """Whether the problem has been present for the whole delay."""
        if self._true_since is None:
            return False
        return dt_util.utcnow() - self._true_since >= self.entity_description.delay_on


class SmaLinkProblem(SmaEntity, BinarySensorEntity):
    """Reports a sustained Modbus outage.

    Unlike the other entities this one must survive the coordinator failing, so
    it is always available and reads the coordinator's link state rather than
    its data.
    """

    _attr_translation_key = "modbus_down"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SmaCoordinator) -> None:
        """Initialize the link problem sensor."""
        super().__init__(coordinator, "modbus_down")

    @property
    def available(self) -> bool:
        """Always available: it reports the outage the others go missing for."""
        return True

    @property
    def is_on(self) -> bool:
        """Whether the link has been down for longer than the grace period."""
        return self.coordinator.link_down
