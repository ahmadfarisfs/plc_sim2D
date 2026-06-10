"""PLC I/O definition and Modbus bit ordering.

Inputs  = sim -> PLC (sensors), exposed as Modbus DISCRETE INPUTS (FC02), addr 0..8
Outputs = PLC -> sim (actuators), exposed as Modbus COILS (FC01/05/15), addr 0..8
"""
from dataclasses import dataclass, fields


@dataclass
class Inputs:
    I_LftA_BeltEnd: bool = False   # DI 0  box at left end of lift A belt
    I_Placon_Full: bool = False    # DI 1  box queued at left end of placon line
    I_Conv_Det: bool = False       # DI 2  box on long conveyor (left side)
    I_LftB_BeltStart: bool = False # DI 3  box at left end of lift B belt
    I_LftB_BeltEnd: bool = False   # DI 4  box at right end of lift B belt
    I_LftA_Up: bool = False        # DI 5  lift A upper limit switch
    I_LftA_Down: bool = False      # DI 6  lift A lower limit switch
    I_LftB_Up: bool = False        # DI 7  lift B upper limit switch
    I_LftB_Down: bool = False      # DI 8  lift B lower limit switch
    I_EStop_NC: bool = True        # DI 9  e-stop, NC contact: 1=healthy, 0=pressed
    I_PB_Start: bool = False       # DI 10 push button: start machine (momentary)
    I_PB_Seq: bool = False         # DI 11 push button: start sequence (momentary)

    def to_bits(self):
        return [bool(getattr(self, f.name)) for f in fields(self)]


@dataclass
class Outputs:
    Q_LftA_Fwd: bool = False   # coil 0  lift A belt forward (+x, toward placon)
    Q_LftA_Rev: bool = False   # coil 1  lift A belt reverse (-x)
    Q_LftA_Up: bool = False    # coil 2  lift A raise
    Q_LftA_Down: bool = False  # coil 3  lift A lower
    Q_LftB_Fwd: bool = False   # coil 4  lift B belt forward (+x)
    Q_LftB_Rev: bool = False   # coil 5  lift B belt reverse (-x, toward long conveyor)
    Q_LftB_Up: bool = False    # coil 6  lift B raise
    Q_LftB_Down: bool = False  # coil 7  lift B lower
    Q_Conv_Run: bool = False   # coil 8  long conveyor run (fixed direction, leftward)
    Q_Lamp_Ready: bool = False # coil 9  panel LED: machine ready

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
