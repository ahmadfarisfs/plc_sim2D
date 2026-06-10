# Conveyor / lifter rig simulator (PLC hardware-in-the-loop)

2D physics simulation (pymunk + pygame) of a two-lift conveyor loop, meant to be
wired to a **real PLC over Modbus TCP** so you can test the PLC logic against
simulated material flow.

```
            placon line (manual push ->)              [box spawn]
 [conveyor A]==========================================[conveyor B]
   ^  lift                                                lift  ^
   v          <- long conveyor (one direction) <-               v
 [conveyor A]==========================================[conveyor B]
```

* **Conveyor A / B**: belt platforms on lifts. Belts run forward (+x) or
  reverse (-x); the lift travels between an upper and lower limit switch.
  At the top they line up with the placon line, at the bottom with the
  long conveyor.
* **Long conveyor**: single direction only (leftward), run/stop.
* **Placon line**: undriven — boxes only move when you push them with the mouse.
* **Boxes**: spawn with SPACE (at the marked spawn point) or right-click
  (anywhere). Nudge/drag them with the left mouse button.
* **Operator panel** (top-left, clickable): E-stop (NC contact, click to
  latch/release), START MACHINE and START SEQUENCE momentary push buttons,
  and a MACHINE READY lamp driven by the PLC. The panel has **no behavior in
  the sim** — buttons are raw inputs, the lamp is a raw output; all logic
  belongs in the PLC.
* Boxes can fall into the pit if the PLC discharges a belt when the lift is
  at the wrong level — that's the point: bad logic is visible.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```sh
# default: Modbus TCP server (slave) on 0.0.0.0:5020 — the PLC polls the sim
.venv/bin/python -m conveyor_sim.main

# standard Modbus port 502 (needs root on macOS/Linux)
sudo .venv/bin/python -m conveyor_sim.main --port 502

# your PLC is the Modbus server instead -> sim polls it
.venv/bin/python -m conveyor_sim.main --mode client --host 192.168.1.10 \
    --in-base 0 --out-base 16

# no PLC: play with keyboard control only
.venv/bin/python -m conveyor_sim.main --mode none
```

## Manual control (no PLC needed)

Press **M** at any time to toggle MANUAL/PLC control (`--mode none` starts in
manual). The HUD always shows live sensor and actuator states.

| key | action |
|-----|--------|
| `1` / `2` | lift A up / down |
| `3` / `4` | conveyor A belt forward / reverse |
| `5` | long conveyor run/stop |
| `6` / `7` | lift B up / down |
| `8` / `9` | conveyor B belt forward / reverse |
| `0` | toggle the READY lamp |
| `SPACE` | spawn box at spawn point |
| right-click | spawn box at mouse |
| left-drag | grab / nudge a box |
| `C` | clear all boxes |
| `M` | toggle manual / PLC control |
| `ESC` | quit |

Keys are toggles; turning on a motion turns off its opposite. Lifts stop
automatically at their limits, motors with both directions commanded stop.

## PLC I/O map (server mode, zero-based addressing)

Sensors — read by the PLC as **discrete inputs (FC02)**. Also packed into
**input register 0 (FC04)**, bit 0 = DI 0, for PLCs that prefer registers.

| addr | signal | meaning |
|------|--------|---------|
| DI 0 | `I_LftA_BeltEnd` | box at left end of conveyor A belt |
| DI 1 | `I_Placon_Full` | box at left end of placon line |
| DI 2 | `I_Conv_Det` | box on long conveyor (left side) |
| DI 3 | `I_LftB_BeltStart` | box at left end of conveyor B belt |
| DI 4 | `I_LftB_BeltEnd` | box at right end of conveyor B belt |
| DI 5 | `I_LftA_Up` | lift A upper limit switch |
| DI 6 | `I_LftA_Down` | lift A lower limit switch |
| DI 7 | `I_LftB_Up` | lift B upper limit switch |
| DI 8 | `I_LftB_Down` | lift B lower limit switch |
| DI 9 | `I_EStop_NC` | e-stop NC contact: 1 = healthy, 0 = pressed |
| DI 10 | `I_PB_Start` | push button: start machine (momentary) |
| DI 11 | `I_PB_Seq` | push button: start sequence (momentary) |

Actuators — written by the PLC as **coils (FC01/FC05/FC15)**. Also mirrored
both ways with **holding register 0 (FC03/FC06)**, bit 0 = coil 0.

| addr | signal | meaning |
|------|--------|---------|
| C 0 | `Q_LftA_Fwd` | conveyor A belt forward (+x, toward placon) |
| C 1 | `Q_LftA_Rev` | conveyor A belt reverse (-x) |
| C 2 | `Q_LftA_Up` | lift A raise |
| C 3 | `Q_LftA_Down` | lift A lower |
| C 4 | `Q_LftB_Fwd` | conveyor B belt forward (+x) |
| C 5 | `Q_LftB_Rev` | conveyor B belt reverse (-x, toward long conveyor) |
| C 6 | `Q_LftB_Up` | lift B raise |
| C 7 | `Q_LftB_Down` | lift B lower |
| C 8 | `Q_Conv_Run` | long conveyor run (always leftward) |
| C 9 | `Q_Lamp_Ready` | panel LED: machine ready |

In **client mode** the same bit order is used: sensor bits are written to PLC
coils starting at `--in-base` (default 0) and actuator commands are read from
PLC coils starting at `--out-base` (default 16). `--device-id` sets the Modbus
unit id (default 1).

## Tests

```sh
.venv/bin/python tests/test_physics.py   # belts, lifts, limits, sensors
.venv/bin/python tests/test_modbus.py    # Modbus loopback (client vs server)
```

## Layout

* `conveyor_sim/world.py` — pymunk physics: belts (friction surface velocity),
  kinematic lifts with limits, proximity sensors (point queries), mouse joint.
  All geometry/speeds are constants at the top of this file.
* `conveyor_sim/plc.py` — Modbus TCP server (self-contained, zero-based) and
  pymodbus-based client mode.
* `conveyor_sim/iomap.py` — signal names and Modbus bit order.
* `conveyor_sim/main.py` — pygame rendering, HUD, keyboard/mouse handling.
