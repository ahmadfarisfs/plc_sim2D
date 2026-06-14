"""PLC I/O definition. Names and order match the real PLC program.

Inputs  = sim -> PLC (sensors). Order matches the PLC's simulation-mode
          M devices: M200..M211 (G_IsSimulated reassignment). Over Modbus
          they are DISCRETE INPUTS (FC02) addr 0..11 in the same order.
Outputs = PLC -> sim (actuators). Order matches the PLC's physical Y
          devices (octal numbering). Over Modbus they are COILS addr 0..10.
"""
from dataclasses import dataclass, fields


@dataclass
class Inputs:
    I_Pnl_EStop: bool = True            # M200  e-stop NC contact: 1=healthy, 0=pressed
    I_Pnl_MachineStartPB: bool = False  # M201  push button: start machine (momentary)
    I_Pnl_SeqStartPB: bool = False      # M202  push button: start sequence (momentary)
    I_LftA_BeltEnd: bool = False        # M203  box at left end of lift A belt
    I_LftA_UpLimit: bool = False        # M204  lift A upper limit switch
    I_LftA_DnLimit: bool = False        # M205  lift A lower limit switch
    I_LftB_BeltStart: bool = False      # M206  box at left end of lift B belt
    I_LftB_BeltEnd: bool = False        # M207  box at right end of lift B belt
    I_LftB_UpLimit: bool = False        # M208  lift B upper limit switch
    I_LftB_DnLimit: bool = False        # M209  lift B lower limit switch
    I_Conv_BeltEnd: bool = False        # M210  box at discharge end of long conveyor
    I_Placon_Full: bool = False         # M211  box queued at left end of placon line
    I_LftA_EmergencyStop: bool = True   # M212  e-stop NC contact at Lifter A: 1=healthy, 0=pressed
    I_LftB_EmergencyStop: bool = True   # M213  e-stop NC contact at Lifter B: 1=healthy, 0=pressed

    def to_bits(self):
        return [bool(getattr(self, f.name)) for f in fields(self)]


@dataclass
class Outputs:
    O_LftA_LiftDown: bool = False   # Y0   lift A lower
    O_LftA_LiftUp: bool = False     # Y1   lift A raise
    O_LftA_BeltRev: bool = False    # Y2   lift A belt reverse (-x)
    O_LftA_BeltFwd: bool = False    # Y3   lift A belt forward (+x, toward placon)
    O_LftB_LiftDown: bool = False   # Y4   lift B lower
    O_LftB_LiftUp: bool = False     # Y5   lift B raise
    O_LftB_BeltRev: bool = False    # Y6   lift B belt reverse (-x, toward long conveyor)
    O_LftB_BeltFwd: bool = False    # Y7   lift B belt forward (+x)
    O_Conv_BeltFwd: bool = False    # Y10  long conveyor run (fixed direction, leftward)
    O_Pnl_MachineReady: bool = False  # Y11  panel lamp: machine ready
    O_Pnl_Alarm: bool = False       # Y16  panel lamp: alarm

    def to_bits(self):
        return [bool(getattr(self, f.name)) for f in fields(self)]

    @classmethod
    def from_bits(cls, bits):
        if not bits:
            return cls()
        names = [f.name for f in fields(cls)]
        return cls(**{n: bool(b) for n, b in zip(names, bits)})


IN_NAMES = [f.name for f in fields(Inputs)]
OUT_NAMES = [f.name for f in fields(Outputs)]
N_IN = len(IN_NAMES)
N_OUT = len(OUT_NAMES)

# MC protocol device map (Mitsubishi). Sensor bits are written as one batch
# to M200.. in IN_NAMES order. Commands are read from the Y devices below;
# numbering is octal on FX-style PLCs (Y7 is followed by Y10).
MC_M_BASE = 200
MC_Y_DEVICES = ["Y0", "Y1", "Y2", "Y3", "Y4", "Y5", "Y6", "Y7",
                "Y10", "Y11", "Y16"]
assert len(MC_Y_DEVICES) == N_OUT

# Jig counter: VAR_GLOBAL_RETAIN M_Jig_Conv_JigCount, Word [Signed] @ D2000.
JIG_COUNT_DEVICE = "D2000"

# Real FX5U input wiring, in IN_NAMES order: SimIO's non-simulated branch
# reads these X devices (some inverted, e.g. `I_LftA_UpLimit := NOT X0`).
# The soft PLC drives these directly so it behaves like the real hardware
# (G_IsSimulated = FALSE), not GX Works3's internal M200.. debug mode.
FX5U_X_DEVICES = [
    ("X15", False),  # I_Pnl_EStop
    ("X16", False),  # I_Pnl_MachineStartPB
    ("X13", False),  # I_Pnl_SeqStartPB
    ("X3", False),   # I_LftA_BeltEnd
    ("X0", True),    # I_LftA_UpLimit  := NOT X0
    ("X1", True),    # I_LftA_DnLimit  := NOT X1
    ("X11", False),  # I_LftB_BeltStart
    ("X12", False),  # I_LftB_BeltEnd
    ("X7", True),    # I_LftB_UpLimit  := NOT X7
    ("X10", True),   # I_LftB_DnLimit  := NOT X10
    ("X2", False),   # I_Conv_BeltEnd
    ("X4", False),   # I_Placon_Full
    ("X5", False),   # I_LftA_EmergencyStop
    ("X14", False),  # I_LftB_EmergencyStop
]
assert len(FX5U_X_DEVICES) == N_IN
