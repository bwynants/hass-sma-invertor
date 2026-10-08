"""Describing the inverter's Modbus link, for the Modbus integration to open.

Since Home Assistant 2026.9 the core Modbus integration hands out units over
connections it shares between integrations (`async_get_unit`), and the old
`get_hub` path is deprecated. So this integration no longer opens a socket of
its own: it describes the inverter's link as backend-neutral `modbus_connection`
params, and `__init__.py` asks for a unit on them. Two integrations describing
the same inverter get the same connection, and their requests serialize behind
its lock.

The connection is opened on first use and reconnects on its own, so an inverter
whose Modbus server is down overnight does not fail the config
entry. It closes when the last config entry holding a unit on it unloads.

TWO UNIT IDs FROM ONE CONNECTION. `__init__.py` asks for a unit 3 and a unit 2
handle on these same params; the shared integration keys connections by
device, so both ride one socket and requests across them are serialized
behind the connection - which is exactly what this inverter needs, because
SS7.2 asks for spacing between transfers and a second socket would defeat it.

RTU OVER TCP IS A SERIAL LINK. `ModbusTcpParams(framer="rtu")` is deprecated
in modbus-connection, and the shared integration canonicalises that spelling
to `ModbusSerialParams` over a `socket://host:port` device. It is built in that
form here, with the same line speed, so a gateway named either way by another
integration compares equal and lands on the same connection.

Isolating this in one module is the point: it is the whole coupling between
the config entry's fields and the shared connection.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from modbus_connection import ModbusSerialParams, ModbusTcpParams

from .const import (
    CONF_FRAMER,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_FRAMER,
    DEFAULT_PORT,
    FRAMER_RTU,
    FRAMER_SOCKET,
)

type ModbusLinkParams = ModbusTcpParams | ModbusSerialParams

# The line speed the Modbus integration gives a socket-carried serial framing.
# It must match, or the shared integration refuses the second holder for
# describing the same device with different link settings.
_SOCKET_SERIAL_BAUDRATE = 115200


def _normalize_host(host: str) -> str:
    """Fold the host to lower case, as modbus-connection does for TCP params.

    The IPv6 scope identifier is left alone, the same way.
    """
    address, separator, scope = host.strip().partition("%")
    return address.lower() + separator + scope


def build_modbus_params(data: Mapping[str, Any]) -> ModbusLinkParams:
    """Build the link params from the config entry, in canonical form.

    Raises `KeyError` with no host and `ValueError` for an unknown framer or
    an unusable port.
    """
    host = _normalize_host(str(data[CONF_HOST]))
    port = int(data.get(CONF_PORT, DEFAULT_PORT))
    framer = str(data.get(CONF_FRAMER, DEFAULT_FRAMER))

    if framer == FRAMER_SOCKET:
        return ModbusTcpParams(host=host, port=port)
    if framer == FRAMER_RTU:
        # An IPv6 literal is bracketed, or its own colons read as the port.
        address = f"[{host}]" if ":" in host else host
        return ModbusSerialParams(
            device=f"socket://{address}:{port}",
            framer="rtu",
            baudrate=_SOCKET_SERIAL_BAUDRATE,
        )
    raise ValueError(f"unknown framer {framer!r}")
