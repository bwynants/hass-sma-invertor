"""Config and options flow for the SMA Inverter (Modbus) integration.

The integration owns its Modbus link (see `connection.py`), so the user gives
the inverter's address here rather than picking a shared connection. Each entry
is probed before it is created, so a wrong host, port or unit id is reported in
the form instead of failing later at setup.

TWO UNIT IDs are asked for, which is unusual and deliberate. On this inverter
reads answer on either unit, but writes to 40016 only work on unit 3, and a
handful of read-only registers are only documented on unit 2. The defaults (3
and 2) are what this hardware wants; they are exposed because another SMA model
may differ.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from modbus_connection import ModbusError

from .connection import create_modbus_connection
from .const import (
    CONF_FALLBACK_TIMEOUT,
    CONF_FRAMER,
    CONF_HOST,
    CONF_PMAX,
    CONF_PORT,
    CONF_UNIT_ID,
    CONF_UNIT_ID_ALT,
    DEFAULT_FALLBACK_TIMEOUT,
    DEFAULT_FRAMER,
    DEFAULT_PMAX,
    DEFAULT_PORT,
    DEFAULT_UNIT_ID,
    DEFAULT_UNIT_ID_ALT,
    DOMAIN,
    FRAMER_RTU,
    FRAMER_SOCKET,
)
from .sma_modbus import (
    FALLBACK_TIMEOUT_MAX_S,
    FALLBACK_TIMEOUT_MIN_S,
    SmaModbusDevice,
)

_PORT = NumberSelector(
    NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
)
_UNIT = NumberSelector(
    NumberSelectorConfig(min=1, max=247, step=1, mode=NumberSelectorMode.BOX)
)
_FRAMER = SelectSelector(
    SelectSelectorConfig(
        options=[FRAMER_SOCKET, FRAMER_RTU],
        mode=SelectSelectorMode.DROPDOWN,
        translation_key="framer",
    )
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): _PORT,
        vol.Required(CONF_FRAMER, default=DEFAULT_FRAMER): _FRAMER,
        vol.Required(CONF_UNIT_ID, default=DEFAULT_UNIT_ID): _UNIT,
        vol.Required(CONF_UNIT_ID_ALT, default=DEFAULT_UNIT_ID_ALT): _UNIT,
    }
)


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    """Build the options schema, defaulted to the current values."""
    return vol.Schema(
        {
            # PMAX is the denominator of every watt-to-percent conversion, so
            # this single number scales every limit the integration writes.
            # The "nominal PV power" diagnostic sensor reads the inverter's own
            # value back (register 41203) as a cross-check.
            vol.Required(
                CONF_PMAX, default=options.get(CONF_PMAX, DEFAULT_PMAX)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=500, max=100000, step=100, mode=NumberSelectorMode.BOX
                )
            ),
            # Also sets the heartbeat period: half of this, clamped to
            # 10..300 s. 600 s is the factory default of register 41525.
            vol.Required(
                CONF_FALLBACK_TIMEOUT,
                default=options.get(
                    CONF_FALLBACK_TIMEOUT, DEFAULT_FALLBACK_TIMEOUT
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=FALLBACK_TIMEOUT_MIN_S,
                    max=FALLBACK_TIMEOUT_MAX_S,
                    step=10,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


async def _async_probe(data: dict[str, Any]) -> bool:
    """Whether an SMA inverter answers with these connection settings."""
    connection = None
    try:
        connection = create_modbus_connection(data)
        unit = connection.for_unit(int(data[CONF_UNIT_ID]))
        await SmaModbusDevice.async_probe(unit)
    except (ModbusError, OSError, ValueError):
        return False
    finally:
        if connection is not None:
            with suppress(ModbusError, OSError):
                await connection.close()
    return True


def _connection_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalise the connection fields from a submitted form."""
    return {
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_PORT: int(user_input[CONF_PORT]),
        CONF_FRAMER: str(user_input[CONF_FRAMER]),
        CONF_UNIT_ID: int(user_input[CONF_UNIT_ID]),
        CONF_UNIT_ID_ALT: int(user_input[CONF_UNIT_ID_ALT]),
    }


class SmaInverterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the inverter's address and probe it."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _connection_data(user_input)
            await self.async_set_unique_id(
                f"{data[CONF_HOST]}:{data[CONF_PORT]}-{data[CONF_UNIT_ID]}"
            )
            self._abort_if_unique_id_configured()

            if await _async_probe(data):
                return self.async_create_entry(title="SMA", data=data)
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user move the inverter to a new address."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _connection_data(user_input)
            if await _async_probe(data):
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, {**entry.data, **(user_input or {})}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> SmaInverterOptionsFlow:
        """Return the options flow."""
        return SmaInverterOptionsFlow()


class SmaInverterOptionsFlow(OptionsFlow):
    """Tunables: the device maximum and the fallback timeout."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the tunables."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
