"""The SMA register map, as ``modbus_connection.model`` components.

Every register on this inverter lives in the HOLDING space (FC03) - there is no
input-register map, which is why all four components declare
``register_space = "holding"``.

FOUR components, split by unit id and by polling cadence:

* :class:`SmaTelemetry`     - unit 3, polled: production, grid, mode, readbacks.
* :class:`SmaGridLimit`     - unit 2, polled: the limit the inverter applies.
* :class:`SmaDiagnostics`   - unit 3, on demand: condition, message, derating.
* :class:`SmaCapabilities`  - unit 2, on demand: nominal power ratings.
* :class:`SmaSettings`      - unit 3, the writable setpoint and config registers.

WHY TWO UNIT IDs: reading works on both unit 2 and unit 3 on this inverter, but
a few registers are only documented on unit 2, and - measured, not assumed -
WRITING to 40016 works only on unit 3 even though the parameter list places
that register on unit 2. Reads and writes genuinely behave differently here, so
the split is deliberate and must not be "simplified" onto one unit.

WHY THE CADENCE SPLIT: SS7.2 (p.68) asks for at least ten seconds between data
transfers and no more than five values per inverter per transfer. The polled
components are kept small for that reason; the diagnostic ones cost nothing
until something asks for them, exactly like the ``scan_interval: 0`` registers
in the YAML setup this replaces.

``register_ranges`` declares only the spans the datapoints actually occupy. The
planner merges reads *within* a range and never bridges a gap, so a poll issues
one block read per contiguous run and never touches an address the inverter may
not answer - which matters here, because an unsupported register returns a NaN
sentinel rather than an error and a write to one vanishes silently.

Addresses are the raw Modbus addresses on the wire (no 3x/4x data-model
offset), taken from this inverter's own parameter list
(PARAMETER-HTML_SBxx-1AV-41-V16) rather than the generic 2014 Modbus-TI
document, which describes an older generation and disagrees in places.

NO SCALING IS DECLARED HERE, on purpose. SMA signals "cannot read this" with a
sentinel value (0xFFFFFFFF, 0x80000000 or 0xFFFFFD) rather than an error, and a
scale factor turns those sentinels into plausible-looking numbers - 0xFFFFFFFF
at scale 0.01 reads as 42949672.95 s. :mod:`sma_modbus.model` therefore takes
the raw integers, rejects them against a plausibility band, and only then
applies the scale. See the comments there.
"""

from __future__ import annotations

from modbus_connection.model import Component, Range, int32, integer, uint32

from .validators import (
    validate_fallback_mode,
    validate_fallback_timeout,
    validate_operating_mode,
    validate_power_limit_pct,
)


class SmaTelemetry(Component):
    """Unit 3: what gets polled every cycle."""

    register_space = "holding"
    register_ranges: tuple[Range, ...] = (
        (30529, 30530),  # total_yield
        (30775, 30776),  # ac_power
        (30783, 30784),  # ac_voltage
        (30795, 30796),  # ac_current
        (30803, 30806),  # grid_frequency + reactive_power (one contiguous run)
        (30813, 30814),  # apparent_power
        (35469, 35470),  # pv_power
        (35547, 35548),  # limit_source
        (40210, 40211),  # operating_mode
        (41193, 41194),  # fallback_mode
        (41525, 41526),  # fallback_timeout
    )

    # Lifetime yield, U32 FIX3 -> kWh after scaling in the model.
    total_yield = uint32(30529)
    # 30775 Measurement.GridMs.TotW: what the inverter puts on the AC side.
    # A CHARGING BATTERY lowers this without any curtailment, so it is not
    # evidence of a limit on its own - compare it with pv_power (35469).
    ac_power = int32(30775)
    ac_voltage = uint32(30783)
    ac_current = uint32(30795)
    grid_frequency = uint32(30803)
    reactive_power = int32(30805)
    apparent_power = int32(30813)
    # 35469 Measurement.PvGen.PvW: the real PV yield, independent of the
    # battery. The difference against ac_power is what goes to the battery, and
    # with the battery registers gone this is the only way to tell a charging
    # battery from actual curtailment.
    pv_power = uint32(35469)
    # 35547: which source imposes the binding limit. Answers "who is limiting",
    # where the limit registers only answer "is something limiting".
    limit_source = uint32(35547)
    # 40210: the operating mode of the active power limitation. This decides
    # whether a setpoint write does anything at all, so it is polled rather
    # than assumed.
    operating_mode = uint32(40210)
    fallback_mode = uint32(41193)
    # 41525 "External active power setpoint, timeout", U32 FIX2.
    fallback_timeout = uint32(41525)


class SmaGridLimit(Component):
    """Unit 2: the limit the inverter actually applies.

    Its own component because it lives on a different unit id, and polled every
    cycle because it is the only value that says whether a written setpoint is
    being HONOURED rather than merely accepted.
    """

    register_space = "holding"
    register_ranges: tuple[Range, ...] = ((31405, 31406),)

    # 31405 "Current spec. active power limitation P" (W), U32 FIX0. Reads the
    # binding limit whatever imposed it - the CEILING being enforced, not the
    # power being produced.
    #
    # U32 and not int32: read as signed, the NaN sentinel decodes to -1 and
    # looks like a real measurement.
    #
    # MEASURED BEHAVIOUR, which contradicts the YAML setup this replaces: with
    # nothing limiting, this reads a clean 5000 W (= WMax) rather than the NaN
    # the old comment predicted. So it is readable at all times and does not
    # need a limit switched on to be tested. Not yet confirmed under active
    # curtailment - the expectation is that it falls to the enforced value.
    # Either way the model's plausibility band handles both: a sentinel is far
    # outside 0..100 kW and comes out as None/unknown.
    power_limit_applied = uint32(31405)


class SmaDiagnostics(Component):
    """Unit 3: read on demand only.

    Reading is always safe - an unsupported register answers NaN rather than
    raising (SS6, p.64) - but it costs bus time, and these values only matter
    when something is being investigated.
    """

    register_space = "holding"
    register_ranges: tuple[Range, ...] = (
        (30201, 30204),  # condition + nominal_power_ok (one contiguous run)
        (30213, 30214),  # message
        (30219, 30220),  # derating
        (30233, 30234),  # wmax
    )

    condition = uint32(30201)
    # 30203: the maximum active power the inverter allows while its status is
    # OK. 31085 measured identical to this on this device - one input on the
    # same value, not a second measurement; do not add it back.
    nominal_power_ok = uint32(30203)
    # 30213 "Message" (TAGLIST). 302 = no message. For a code's meaning see
    # 30215 "Fault correction measure" and the device manual.
    message = uint32(30213)
    derating = uint32(30219)
    wmax = uint32(30233)


class SmaCapabilities(Component):
    """Unit 2: nameplate ratings, read on demand.

    31547 quantifies curtailment directly. 41203 was meant to verify the PMAX
    assumption behind the watt-to-percent conversion, but reports 0 on this
    device - see the comment on the field. WMax (30233, in
    :class:`SmaDiagnostics`) is the register that actually answers, and a wrong
    PMAX silently rescales every limit written, so it is worth one read.
    """

    register_space = "holding"
    register_ranges: tuple[Range, ...] = (
        (31547, 31548),  # available_power
        (41203, 41204),  # nominal_pv_power
    )

    # 31547 "Available inverter power" (W): what the inverter COULD deliver.
    # Together with ac_power this quantifies curtailment directly instead of
    # inferring it from a setpoint.
    available_power = uint32(31547)
    # 41203 "Nominal PV system power" (W). The parameter list defaults it to
    # 7680 W, but this device reports 0 - measured. It is read because it is
    # cheap and other models may populate it; it is NOT a usable PMAX
    # cross-check here. Use 30233 (WMax) for that.
    nominal_pv_power = uint32(41203)


class SmaSettings(Component):
    """Unit 3: everything this library may write.

    TWO CLASSES OF REGISTER, and the difference is the whole safety story:

    * 40016 is a grid-management setpoint ("Device Control Object"). It is
      write-only, cheap, and MEANT to be written repeatedly - a heartbeat on it
      is correct.
    * 40210, 41193 and 41525 are flash configuration registers. SS2.3 (p.9):
      "Cyclical changing of these parameters leads to destruction of the flash
      memory of the devices." They are written once, by hand, and the device
      guards each write against a no-op (see :meth:`SmaModbusDevice.async_set_*`).

    40212 and 40214 - the watt and percent power-limit *configuration*
    registers - are DELIBERATELY ABSENT from this map. They are flash registers,
    and a power limit that follows the sun would rewrite them all day. That also
    rules out operating modes 1077 and 1078, whose setpoints live there.

    THIS COMPONENT IS NEVER READ. It is deliberately left out of the device's
    ``ComponentGroup``s and nothing calls ``async_update()`` on it, because
    40016 is WRITE-ONLY: a read there answers with a NaN sentinel at best. The
    ranges below exist so the framework can plan and validate *writes*; the
    readbacks all live in :class:`SmaTelemetry` instead.
    """

    register_space = "holding"
    register_ranges: tuple[Range, ...] = (
        (40016, 40016),
        (40210, 40211),
        (41193, 41194),
        (41525, 41526),
    )

    # 40016 "Normalized active power limitation by PV system control", S16 FIX0,
    # WRITE-ONLY, unit 3. Whole percent of WMax. Only effective once mode 1079
    # has been set AND the inverter restarted; without that restart the write is
    # accepted and silently ignored.
    #
    # THERE IS NO READBACK OF THIS REGISTER. 41255 mirrored it and was read for
    # exactly that reason, but it has been removed on request. What remains is
    # 35547 (limit_source), which reports WHETHER our setpoint is the binding
    # limit without reporting its VALUE. So a write that is accepted and then
    # ignored is no longer directly detectable; if a limit stops taking effect,
    # check 35547 and the mode, and remember that mode 1079 needs an inverter
    # restart before 40016 does anything at all.
    power_limit_pct = integer(40016, signed=True, writable=validate_power_limit_pct)
    # --- flash configuration below: one write per actual change, never cyclic --
    operating_mode = uint32(40210, writable=validate_operating_mode)
    fallback_mode = uint32(41193, writable=validate_fallback_mode)
    # U32 FIX2: seconds x 100, so 600 s is 60000 and 1800 s is 180000.
    #
    # This is the ONE field that declares a scale, and the asymmetry is
    # deliberate. A validator sees engineering units, so with scale=0.01 the
    # library encodes 600 s as 60000 for us and the FIX2 factor is stated once
    # instead of being open-coded at the call site. The READ side of the same
    # register (:attr:`SmaTelemetry.fallback_timeout`) stays unscaled, because
    # there a scale would turn the NaN sentinel into a plausible 42949672.95 s
    # before the model ever gets to reject it.
    fallback_timeout = uint32(
        41525, scale=0.01, unit="s", writable=validate_fallback_timeout
    )
