# SMA Inverter (Modbus)

A Home Assistant integration for SMA hybrid inverters over Modbus TCP, built the
way the [Modbus modernization post][post] asks for: a **device library** that owns
the protocol knowledge, and a thin integration that wires it to entities.

Developed against a **Sunny Boy Smart Energy 5.0** (ennexOS generation). 

[post]: https://developers.home-assistant.io/blog/2026/07/05/modernizing-modbus/

## ⚠ Read this before enabling it

**Two flash registers are written**, by button press only — 40210 and
41193/41525. The device library refuses a write that would not change anything,
so a double press is free, but do not drive those buttons from an automation.

## Installation

### Prerequisites

- Home Assistant 2026.9.0 or newer
- An SMA inverter reachable over **Modbus TCP** (enable *Modbus TCP server* in the
  inverter's web UI, default port 502)

### HACS (recommended)

1. In HACS, open the overflow menu → **Custom repositories**.
2. Add `https://github.com/bwynants/hass-sma-invertor` with type **Integration**.
3. Install **SMA Inverter (Modbus)** and restart Home Assistant.

### Manual installation

1. Copy `custom_components/sma_inverter/` into your HA `config/custom_components/`
   directory:
   ```
   config/
   └── custom_components/
       └── sma_inverter/
           ├── __init__.py
           ├── manifest.json
           ├── config_flow.py
           ├── connection.py
           ├── coordinator.py
           ├── const.py
           ├── entity.py
           ├── sensor.py
           ├── binary_sensor.py
           ├── number.py
           ├── select.py
           ├── switch.py
           ├── button.py
           └── sma_modbus/
               ├── __init__.py
               ├── components.py
               ├── device.py
               ├── model.py
               ├── enums.py
               ├── validators.py
               └── exceptions.py
   ```
2. Restart Home Assistant.

### Setup

Go to **Settings → Devices & Services → Add Integration** and search for
**SMA Inverter (Modbus)**. See [Configuration](#configuration) for what the
fields mean.

## Design

```
sma_modbus/          the device library — no Home Assistant import anywhere
  components.py      the register map, as modbus_connection.model components
  model.py           the snapshot, and the NaN-sentinel rejection
  device.py          the device: async_update(), async_set_*()
  enums.py           operating mode, limit source, fallback mode, derating
  validators.py      write-value validation, on engineering units
*.py                 the integration — connection, coordinator, entities
```

The library is backend-neutral (pymodbus, tmodbus, or the in-memory mock) and
holds no policy. Curtailment policy — the mode gate, the heartbeat, the
watt-to-percent conversion — lives in `coordinator.py`.

### What the register map knows

Everything that YAML package learned the hard way, now in one place:

- **All registers are HOLDING registers** (FC03). There is no input-register map.
- **Two unit ids.** Reads answer on unit 2 *and* 3, but writing to 40016 works
  only on **unit 3** — measured, repeatedly — even though the parameter list puts
  that register on unit 2. Reads and writes genuinely differ here.
- **Mode 1079 is the only writable mode.** 40016 only does anything in that mode,
  and it needs an **inverter restart** after the mode is set or writes are
  silently ignored. In 1078/1077 the setpoint is a *flash* register (40214/40212),
  so this integration writes nothing there.
- **NaN is not an error.** An unreadable register returns `0xFFFFFFFF`,
  `0x80000000` or `0xFFFFFD` rather than raising, so `model.py` rejects values
  against a plausibility band and returns `None`. That is why no scale factor is
  declared in `components.py`: a scale turns a sentinel into a plausible number
  before anything can catch it.
- **A half-applied mode change** clamps the inverter to 20% of WMax and makes
  every write a no-op. If nothing happens after a mode change, restart the
  inverter before debugging anything else.

### Polling

One poll is **13 block reads** for 18 datapoints, because reads are pooled within
the declared `register_ranges`. Diagnostic registers (condition, derating,
message, WMax, nominal power, available power) are **read once at setup and then
never polled** — the "Refresh diagnostics" button re-reads them, which also gives
a *synchronous* snapshot. That matters: polled separately, PV production and AC output can be a
minute apart, and under moving cloud a comparison between them is worthless.

## Entities

| | |
|---|---|
| `number.sma_power_limit` | The limit, **in watts**. At maximum it means "no limit" and the heartbeat stops. |
| `switch.sma_heartbeat` | Keep refreshing the setpoint. Only meaningful with an armed fallback. |
| `select.sma_target_operating_mode` | What the mode button will write. 1079 or off — **1078/1077 are deliberately not offered**. |
| `select.sma_target_fallback_mode` | 2507 (lapses, heartbeat needed) or 2506 (persists). |
| `button.sma_write_operating_mode` | ⚠ flash. One write per real change. |
| `button.sma_write_fallback_config` | ⚠ flash. Timeout first, then mode, 10 s apart. Takes ~20 s. |
| `button.sma_refresh_diagnostics` | Read the on-demand registers now. |
| `binary_sensor.sma_readback_unusable` | 40210 answered with a sentinel — the mode-dependent checks are blind. |
| `binary_sensor.sma_no_safe_fallback` | 41193 = 2506, so a written limit persists after HA stops. |
| `binary_sensor.sma_modbus_link_down` | Sustained outage; stays available when everything else goes away. |

Plus production, grid and diagnostic sensors.

## Configuration

- **Host / port / framer**, and **two unit ids** (defaults 3 and 2).
- **Maximum power (PMAX)** — the denominator of every watt-to-percent conversion,
  so a wrong value scales every limit written. Cross-check it against the **WMax**
  sensor (register 30233). *Not* against *Nominal PV power* (41203): that register
  reports **0** on the Sunny Boy Smart Energy 5.0 — measured — so it cannot serve
  as a reference, and it is disabled by default for that reason.
- **Fallback timeout** — written to 41525, and it also sets the heartbeat period
  at **half** its value (clamped 10–300 s). 600 s is the factory default.

## Enabling debug logging

```yaml
logger:
  logs:
    custom_components.sma_inverter: debug
    sma_modbus: debug
    modbus_connection: debug
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
