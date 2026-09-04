"""Constants for the SMA Inverter (Modbus) integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "sma_inverter"

# Config entry keys (connection)
CONF_HOST = "host"
CONF_PORT = "port"
CONF_FRAMER = "framer"
CONF_UNIT_ID = "unit_id"
CONF_UNIT_ID_ALT = "unit_id_alt"

FRAMER_SOCKET = "socket"
FRAMER_RTU = "rtu"

DEFAULT_PORT = 502
DEFAULT_FRAMER = FRAMER_SOCKET
# Unit 3 answers reads AND writes on this inverter; unit 2 is where a handful of
# read-only registers are documented. Both are needed - see connection.py.
DEFAULT_UNIT_ID = 3
DEFAULT_UNIT_ID_ALT = 2

# Options keys (tunables) + defaults
CONF_PMAX = "pmax_watt"
CONF_FALLBACK_TIMEOUT = "fallback_timeout_seconds"

# PMAX is the single place the device maximum lives: it is the denominator of
# every watt-to-percent conversion, so getting it wrong scales every limit the
# integration writes. Register 41203 reads it back from the inverter as a check
# (see the "nominal PV power" diagnostic sensor).
DEFAULT_PMAX = 5000.0
# 600 s is the factory default of register 41525.
DEFAULT_FALLBACK_TIMEOUT = 600

# -- polling ---------------------------------------------------------------
# SS7.2 (p.68): "the time period between data transfers must be at least ten
# seconds. No more than five parameters and measured values should be
# transmitted per inverter." A poll here is 13 block reads rather than 21
# independently-timed sensor reads, so the aggregate load is far below what the
# YAML setup this replaces was producing - but the interval still stays well
# clear of the floor rather than sitting on it.
UPDATE_INTERVAL = timedelta(seconds=30)

# -- policy timing ---------------------------------------------------------
# Slider drag protection. SS7.2 wants >=10 s between transfers; a 2 s debounce
# collapses a drag into one write instead of one per step.
WRITE_DEBOUNCE_SECONDS = 2.0

# The heartbeat period is DERIVED from the fallback timeout, not fixed: half the
# timeout, clamped into this band. At the factory default of 600 s that gives
# 300 s. SS5.2 (p.19) says the fallback interval restarts on RECEIPT of the
# parameter, so every write resets the clock - including a slider push, which is
# why the coordinator measures from the last write of any kind.
HEARTBEAT_MIN_SECONDS = 10
HEARTBEAT_MAX_SECONDS = 300
HEARTBEAT_FALLBACK_SECONDS = 30  # used when the timeout is implausible
# How often the heartbeat task wakes to decide whether a write is due. A tick
# that is not due costs nothing - no Modbus traffic, just a comparison.
HEARTBEAT_TICK_SECONDS = 10

# How long the link must stay down before the connectivity sensor reports a
# problem. Mirrors the 2-minute delay_on of the YAML setup: a short flap
# self-heals and should not raise an alarm.
LINK_DOWN_GRACE = timedelta(minutes=2)

# A mode change needs the inverter's reaction time (SS7.2: 5-10 s) before a
# readback means anything, so a config write waits this long and then refreshes.
CONFIG_SETTLE_SECONDS = 12
# SS7.2 spacing between the two writes of the fallback pair (timeout, then mode).
CONFIG_WRITE_SPACING_SECONDS = 10

# -- problem detection -----------------------------------------------------
# A detector needs longer than the poll interval plus the inverter's reaction
# time before an apparent problem is real rather than a readback that has not
# caught up.
PROBLEM_DELAY_ON = timedelta(minutes=5)
