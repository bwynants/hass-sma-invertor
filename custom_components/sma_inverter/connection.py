"""Building the Modbus connection this integration owns.

Home Assistant has no shared `modbus_connection` integration yet - the Modbus
modernization blog post asks integrations to hold off on wiring one up - so the
inverter's link is created and owned here, exactly as the reference
implementation does it.

The connection is built but deliberately *not* opened: `modbus-connection`
connects on first use and reconnects on its own, so an inverter that is asleep
at Home Assistant start does not fail the config entry. That matters more here
than for a mains-powered device: a PV inverter's Modbus server can be down
overnight.

TWO UNIT IDs FROM ONE CONNECTION. `for_unit()` hands out a handle per unit id
over the same socket, and requests across handles are serialized behind the
connection - which is exactly what this inverter needs, because SS7.2 asks for
spacing between transfers and a second socket would defeat that.

Isolating this in one module is the point. Whenever the shared integration does
land, borrowing units from it replaces this file and nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from modbus_connection import ModbusTcpParams
from modbus_connection.pymodbus import ModbusConnection

from .const import CONF_FRAMER, CONF_HOST, CONF_PORT, DEFAULT_FRAMER, DEFAULT_PORT


def build_modbus_params(data: Mapping[str, Any]) -> ModbusTcpParams:
    """Build backend-neutral connection parameters from the config entry."""
    framer = cast(Literal["socket", "rtu"], str(data.get(CONF_FRAMER, DEFAULT_FRAMER)))
    return ModbusTcpParams(
        host=str(data[CONF_HOST]),
        port=int(data.get(CONF_PORT, DEFAULT_PORT)),
        framer=framer,
    )


def create_modbus_connection(data: Mapping[str, Any]) -> ModbusConnection:
    """Create the inverter's connection without opening it."""
    return ModbusConnection(build_modbus_params(data))
