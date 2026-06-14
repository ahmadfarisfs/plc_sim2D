# Conveyor / lifter rig simulator (PLC hardware-in-the-loop)

2D physics simulation (pymunk + pygame) of a two-lift conveyor loop, meant to be
wired to a **real PLC** — Mitsubishi MC protocol (pymcprotocol) or Modbus TCP —
so you can test the PLC logic against simulated material flow.

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
  MACHINE READY and ALARM lamps driven by the PLC. The panel has **no
  behavior in the sim** — buttons are raw inputs, lamps are raw outputs;
  all logic belongs in the PLC.
* **Lifter E-stops**: each lift (A and B) has its own mushroom E-stop button
  drawn above it (NC contact, click to latch/release), independent of the
  panel E-stop.
* Boxes can fall into the pit if the PLC discharges a belt when the lift is
  at the wrong level — that's the point: bad logic is visible.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```sh
# Mitsubishi PLC over MC protocol (3E frame). PLC must run G_IsSimulated=TRUE
.venv/bin/python -m conveyor_sim.main --mode mc --host 192.168.1.10 --port 5007

# Modbus TCP server (slave) on 0.0.0.0:5020 — a Modbus-client PLC polls the sim
.venv/bin/python -m conveyor_sim.main

# Modbus: your PLC is the server instead -> sim polls it
.venv/bin/python -m conveyor_sim.main --mode client --host 192.168.1.10 \
    --in-base 0 --out-base 16

# soft PLC: run the GX Works3 PLCopen XML export in-process, no PLC needed
.venv/bin/python -m conveyor_sim.main --mode soft --plc-xml Kawai120626.xml

# no PLC: play with keyboard/buttons only
.venv/bin/python -m conveyor_sim.main --mode none
```

## Mitsubishi MC protocol mode (`--mode mc`)

Matches the PLC program's simulation-mode input reassignment
(`I_x := SEL(G_IsSimulated, Xn, M20n)`):

* sensor bits are **batch-written to M200..M211** (`--m-base` to move),
* commands are **batch-read from Y0..Y17** and picked per the map below,
* `--plctype` selects the pymcprotocol series (`Q` default, also `L`, `QnA`,
  `iQ-L`, `iQ-R`), `--port` is the MC/SLMP port configured in the PLC
  (default 5007),
* Y numbering is treated as **octal** (FX style: Y7 -> Y10). Use
  `--y-radix hex` if your Y map is hexadecimal (Q/iQ I/O numbering).

Target PLC is an **FX5U-64MT/ES**: the defaults (`--plctype Q`,
`--y-radix oct`) are correct. In GX Works3 add an SLMP connection under
*Parameter > FX5UCPU > Module Parameter > Ethernet Port > External Device
Configuration*: protocol **TCP**, communication data code **Binary**, and the
port number you pass as `--port`. Set `G_IsSimulated := TRUE` so the PLC reads
its inputs from M200..M211 instead of the physical X terminals.

Sensors, sim -> PLC:

| M device | signal | meaning |
|------|--------|---------|
| M200 | `I_Pnl_EStop` | e-stop NC contact: 1 = healthy, 0 = pressed |
| M201 | `I_Pnl_MachineStartPB` | push button: start machine (momentary) |
| M202 | `I_Pnl_SeqStartPB` | push button: start sequence (momentary) |
| M203 | `I_LftA_BeltEnd` | box at left end of conveyor A belt |
| M204 | `I_LftA_UpLimit` | lift A upper limit switch |
| M205 | `I_LftA_DnLimit` | lift A lower limit switch |
| M206 | `I_LftB_BeltStart` | box at left end of conveyor B belt |
| M207 | `I_LftB_BeltEnd` | box at right end of conveyor B belt |
| M208 | `I_LftB_UpLimit` | lift B upper limit switch |
| M209 | `I_LftB_DnLimit` | lift B lower limit switch |
| M210 | `I_Conv_BeltEnd` | box at discharge end of long conveyor |
| M211 | `I_Placon_Full` | box at left end of placon line |
| M212 | `I_LftA_EmergencyStop` | e-stop NC contact at Lifter A: 1 = healthy, 0 = pressed |
| M213 | `I_LftB_EmergencyStop` | e-stop NC contact at Lifter B: 1 = healthy, 0 = pressed |

Commands, PLC -> sim:

| Y device | signal | meaning |
|------|--------|---------|
| Y0 | `O_LftA_LiftDown` | lift A lower |
| Y1 | `O_LftA_LiftUp` | lift A raise |
| Y2 | `O_LftA_BeltRev` | conveyor A belt reverse (-x) |
| Y3 | `O_LftA_BeltFwd` | conveyor A belt forward (+x, toward placon) |
| Y4 | `O_LftB_LiftDown` | lift B lower |
| Y5 | `O_LftB_LiftUp` | lift B raise |
| Y6 | `O_LftB_BeltRev` | conveyor B belt reverse (-x, toward long conveyor) |
| Y7 | `O_LftB_BeltFwd` | conveyor B belt forward (+x) |
| Y10 | `O_Conv_BeltFwd` | long conveyor run (always leftward) |
| Y11 | `O_Pnl_MachineReady` | panel lamp: machine ready |
| Y16 | `O_Pnl_Alarm` | panel lamp: alarm |

## Soft PLC mode (`--mode soft --plc-xml <file>`)

No hardware or network PLC at all: `--plc-xml` points at a **PLCopen XML**
export of the GX Works3 project (Project > right-click > Export to PLCopen
XML format). [conveyor_sim/softplc/](conveyor_sim/softplc/) parses that file —
global labels, POUs and their Structured Text bodies, and the
initial/scan execution order — and runs the actual program logic each frame,
wired the same way the real **FX5U** hardware is (`G_IsSimulated := FALSE`):

* sensor bits are written to the **X input devices** SimIO's real-input
  branch reads (`X15`, `X16`, `X13`, `X3`, `X0`, `X1`, `X11`, `X12`, `X7`,
  `X10`, `X2`, `X4`, `X5`, `X14` — see `FX5U_X_DEVICES` in
  [conveyor_sim/iomap.py](conveyor_sim/iomap.py)), with the same
  `NOT`-polarity as the wiring (e.g. `I_LftA_UpLimit := NOT X0`),
* `O_*` outputs are read back by name from the program's global variables
  (each is directly mapped to a `Y` device, e.g. `O_Pnl_MachineReady` =
  `Y11`).

The soft PLC runtime itself ([conveyor_sim/softplc/runtime.py](conveyor_sim/softplc/runtime.py))
is generic — it doesn't special-case `G_IsSimulated` or any other variable,
it just runs the program with whatever initial values the XML declares.
`SoftPlcLink` is what makes it behave like this specific FX5U.

The interpreter supports the subset of ST this project uses: `IF/ELSIF/ELSE`,
`CASE...OF`, assignments, `RETURN`, `AND/OR/NOT` and comparison/arithmetic
expressions, dotted member access (e.g. `TP_Eject.S`), and the `OUT_T`
(TON-style timer) / `PLS` (rising-edge pulse) function calls. If you edit the
program in GX Works3 and stay within that subset, just re-export the XML and
re-run — no code changes needed. The HUD status line shows the soft PLC's
live state (`M_Machine_Ready`, `M_LftA_State`, `M_Lftb_State`).

## Modbus TCP mode (zero-based addressing)

Same signals, same order. Sensors are **discrete inputs 0..11** (FC02, M200
order; also packed into input register 0, FC04). Commands are **coils 0..10**
(FC01/05/15, Y-device order; also mirrored both ways with holding register 0,
FC03/06). In `--mode client` the sensor bits are written to PLC coils at
`--in-base` and commands read from coils at `--out-base`.

## Manual control (no PLC needed)

Press **M** at any time to toggle MANUAL/PLC control (`--mode none` starts in
manual). Click the small buttons under each conveyor, or use the keys.
Clicking a motor button while a PLC is connected switches to manual.
The HUD always shows live sensor and actuator states with their device
addresses.

| key | action |
|-----|--------|
| `1` / `2` | lift A up / down |
| `3` / `4` | conveyor A belt forward / reverse |
| `5` | long conveyor run/stop |
| `6` / `7` | lift B up / down |
| `8` / `9` | conveyor B belt forward / reverse |
| `0` | toggle the READY lamp |
| `-` | toggle the ALARM lamp |
| `SPACE` | spawn box at spawn point |
| right-click | spawn box at mouse |
| left-drag | grab / nudge a box |
| `C` | clear all boxes |
| `M` | toggle manual / PLC control |
| `ESC` | quit |
| click E-STOP A/B | latch/release the lift-side e-stops |

Keys and buttons are toggles; turning on a motion turns off its opposite.
Lifts stop automatically at their limits, motors with both directions
commanded stop.

## Tests

```sh
.venv/bin/python tests/test_physics.py   # belts, lifts, limits, sensors
.venv/bin/python tests/test_modbus.py    # Modbus loopback (client vs server)
.venv/bin/python tests/test_softplc.py   # soft PLC: runs the exported ST program
```

## Layout

* `conveyor_sim/world.py` — pymunk physics: belts (friction surface velocity),
  kinematic lifts with limits, proximity sensors (point queries), mouse joint.
  All geometry/speeds are constants at the top of this file.
* `conveyor_sim/iomap.py` — signal names (matching the PLC program) and the
  M/Y device map and bit order.
* `conveyor_sim/plc.py` — MC protocol client, Modbus TCP server
  (self-contained, zero-based), Modbus client mode, and the soft PLC link.
* `conveyor_sim/softplc/` — soft PLC: PLCopen XML loader (`project.py`), a
  Structured Text lexer/parser/interpreter (`st_lang.py`), and the scan
  runtime (`runtime.py`).
* `conveyor_sim/main.py` — pygame rendering, HUD, panel, buttons,
  keyboard/mouse handling.
