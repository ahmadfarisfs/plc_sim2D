"""Soft PLC checks: load the exported GX Works3 XML and run its ST logic
directly, no PLC hardware involved."""
import os
import sys

sys.path.insert(0, ".")

from conveyor_sim.iomap import FX5U_X_DEVICES, IN_NAMES, N_IN, OUT_NAMES
from conveyor_sim.plc import SoftPlcLink
from conveyor_sim.softplc import SoftPlc

XML_PATH = os.path.join(os.path.dirname(__file__), "..", "Kawai120626.xml")


def set_inputs(plc, **inputs):
    """Drive IN_NAMES bits onto the X devices SimIO's real-input branch
    reads, applying the wiring's NOT-polarity (I_Pnl_EStop defaults
    healthy/True)."""
    values = {name: False for name in IN_NAMES}
    values["I_Pnl_EStop"] = True
    values["I_LftA_EmergencyStop"] = True
    values["I_LftB_EmergencyStop"] = True
    values.update(inputs)
    for name, (device, invert) in zip(IN_NAMES, FX5U_X_DEVICES):
        bit = values[name]
        plc.set_device(device, (not bit) if invert else bit)


def start_machine(plc):
    set_inputs(plc, I_Pnl_MachineStartPB=True)
    plc.step(dt=0.0)
    assert plc.get("M_Machine_Ready") is True


def run_eject_timer(plc, eject_time_var):
    """Advance the eject timer (G_Lft*_EjectTime, K-units of 100ms) without
    changing other inputs."""
    seconds = plc.get(eject_time_var) * 0.1
    whole = int(seconds)
    for _ in range(whole - 1):
        plc.step(dt=1.0)
    plc.step(dt=1.0)   # et reaches `seconds`, TP_Eject.S becomes True
    plc.step(dt=0.0)   # next scan sees TP_Eject.S and transitions state


def test_initvar_and_simio():
    plc = SoftPlc(XML_PATH)
    assert plc.get("G_IsSimulated") is False  # XML default: real FX5U wiring
    assert plc.get_device("M200") is False    # InitVar's M200:=TRUE is sim-mode only

    set_inputs(plc)
    plc.step(dt=0.0)
    assert plc.get("I_Pnl_EStop") is True      # SimIO real-input branch: I_Pnl_EStop := X15
    assert plc.get("I_LftA_UpLimit") is False  # := NOT X0 (X0 driven True by set_inputs)
    print("  InitVar/SimIO: G_IsSimulated defaults FALSE, I_* <- X devices OK")


def test_machine_ready_and_estop():
    plc = SoftPlc(XML_PATH)

    set_inputs(plc)
    plc.step(dt=0.0)
    assert plc.get("M_Machine_Ready") is False
    assert plc.get("O_Pnl_MachineReady") is False

    start_machine(plc)
    assert plc.get("O_Pnl_MachineReady") is True
    assert plc.get("O_Pnl_Alarm") is False
    print("  Safety: START PB latches M_Machine_Ready, lamps follow OK")

    set_inputs(plc, I_Pnl_EStop=False)
    plc.step(dt=0.0)
    assert plc.get("M_Machine_Ready") is False
    assert plc.get("O_Pnl_Alarm") is True
    assert plc.get("O_Pnl_MachineReady") is False
    assert plc.get("M_LftA_State") == 0
    assert plc.get("M_Lftb_State") == 0
    assert plc.get("O_LftA_LiftUp") is False
    print("  Safety: E-stop clears outputs, alarm on, states reset OK")


def test_feedlifter_a_cycle():
    plc = SoftPlc(XML_PATH)
    start_machine(plc)
    assert plc.get("M_LftA_State") == 0   # ST_Homing

    set_inputs(plc, I_LftA_DnLimit=True)
    plc.step(dt=0.0)
    assert plc.get("M_LftA_State") == 1, "Homing -> Idle on down limit"

    set_inputs(plc, I_LftA_DnLimit=True, I_Conv_BeltEnd=True)
    plc.step(dt=0.0)
    assert plc.get("M_LftA_State") == 2, "Idle -> Inserting"
    assert plc.get("O_LftA_BeltFwd") is True
    assert plc.get("O_Conv_BeltFwd") is True

    set_inputs(plc, I_LftA_BeltEnd=True, I_Conv_BeltEnd=True)
    plc.step(dt=0.0)
    assert plc.get("M_LftA_State") == 3, "Inserting -> Lifting"
    assert plc.get("O_LftA_LiftUp") is True
    assert plc.get("O_LftA_BeltFwd") is False

    set_inputs(plc, I_LftA_UpLimit=True)
    plc.step(dt=0.0)
    assert plc.get("M_LftA_State") == 4, "Lifting -> WaitingEject"

    set_inputs(plc, I_LftA_UpLimit=True, I_Placon_Full=False)
    plc.step(dt=0.0)
    assert plc.get("M_LftA_State") == 5, "WaitingEject -> Ejecting"
    assert plc.get("O_LftA_BeltRev") is True

    set_inputs(plc, I_LftA_UpLimit=True)
    run_eject_timer(plc, "G_LftA_EjectTime")
    assert plc.get("M_LftA_State") == 6, "Ejecting -> Descend after the eject timer"
    assert plc.get("O_LftA_LiftDown") is True

    set_inputs(plc, I_LftA_DnLimit=True)
    plc.step(dt=0.0)
    assert plc.get("M_LftA_State") == 1, "Descend -> Idle"
    print("  FeedLifterA: Homing -> Idle -> Inserting -> Lifting -> "
          "WaitingEject -> Ejecting -> Descend -> Idle OK")


def test_feedlifter_b_cycle():
    plc = SoftPlc(XML_PATH)
    start_machine(plc)
    assert plc.get("M_Lftb_State") == 0   # ST_Homing

    set_inputs(plc, I_LftB_UpLimit=True)
    plc.step(dt=0.0)
    assert plc.get("M_Lftb_State") == 1, "Homing -> Idle"

    set_inputs(plc, I_LftB_BeltStart=True)
    plc.step(dt=0.0)
    assert plc.get("M_Lftb_State") == 2, "Idle -> Inserting"
    assert plc.get("O_LftB_BeltRev") is True

    set_inputs(plc, I_LftB_BeltEnd=True)
    plc.step(dt=0.0)
    assert plc.get("M_Lftb_State") == 3, "Inserting -> WaitingDescend"
    assert plc.get("O_LftB_BeltRev") is False

    set_inputs(plc, I_Pnl_SeqStartPB=True)
    plc.step(dt=0.0)
    assert plc.get("M_Lftb_State") == 4, "WaitingDescend -> Descend"
    assert plc.get("O_LftB_LiftDown") is True

    set_inputs(plc, I_LftB_DnLimit=True)
    plc.step(dt=0.0)
    assert plc.get("M_Lftb_State") == 5, "Descend -> Ejecting"
    assert plc.get("O_LftB_BeltFwd") is True

    set_inputs(plc, I_LftB_DnLimit=True)
    run_eject_timer(plc, "G_LftB_EjectTime")
    assert plc.get("M_Lftb_State") == 6, "Ejecting -> Lifting after the eject timer"
    assert plc.get("O_LftB_LiftUp") is True

    set_inputs(plc, I_LftB_UpLimit=True)
    plc.step(dt=0.0)
    assert plc.get("M_Lftb_State") == 1, "Lifting -> Idle"
    print("  FeedLifterB: Homing -> Idle -> Inserting -> WaitingDescend -> "
          "Descend -> Ejecting -> Lifting -> Idle OK")


def test_link_round_trip():
    link = SoftPlcLink(XML_PATH)
    assert link._plc.get("G_IsSimulated") is False  # hw-equivalent: real X/Y wiring

    bits = [False] * N_IN
    bits[IN_NAMES.index("I_Pnl_EStop")] = True
    bits[IN_NAMES.index("I_Pnl_MachineStartPB")] = True
    link.push_inputs(bits)

    # I_LftA_UpLimit := NOT X0; bit is False, so X0 must be driven True.
    assert link._plc.get_device("X0") is True
    assert link._plc.get("I_LftA_UpLimit") is False
    # I_Pnl_EStop := X15 (no inversion)
    assert link._plc.get_device("X15") is True
    assert link._plc.get("I_Pnl_EStop") is True

    outs = link.pull_outputs()
    assert len(outs) == len(OUT_NAMES)
    assert outs[OUT_NAMES.index("O_Pnl_MachineReady")] is True
    print("  SoftPlcLink: FX5U X-device wiring, push_inputs/pull_outputs OK")


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items()
                            if k.startswith("test_")}.items()):
        fn()
    print("all softplc tests passed")
