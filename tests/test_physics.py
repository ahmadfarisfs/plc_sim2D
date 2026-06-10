"""Headless physics checks: belts drive boxes, lifts travel, sensors fire."""
import sys

sys.path.insert(0, ".")

from conveyor_sim import world as wd
from conveyor_sim.iomap import Outputs

DT = 1.0 / 60.0


def run(world, outs, frames):
    ins = None
    for _ in range(frames):
        ins = world.update(outs, DT)
    return ins


def on_surface(x, y_surface):
    return (x, y_surface - wd.BOX_H / 2 - 1)


def test_long_conveyor_moves_box_left():
    w = wd.World()
    b = w.spawn_box(on_surface(700, wd.Y_BOT))
    run(w, Outputs(), 30)
    x0 = b.position.x
    run(w, Outputs(Q_Conv_Run=True), 120)
    dx = b.position.x - x0
    assert dx < -50, f"box should move left on long conveyor, dx={dx:.1f}"
    print(f"  long conveyor: box moved {dx:.1f}px (leftward) OK")


def test_conv_det_sensor():
    w = wd.World()
    w.spawn_box(on_surface(wd.LONG_X0 + 50, wd.Y_BOT))
    ins = run(w, Outputs(), 30)
    assert ins.I_Conv_Det, "I_Conv_Det should see the box"
    assert not ins.I_Placon_Full and not ins.I_LftB_BeltStart
    print("  I_Conv_Det fires for box on long conveyor OK")


def test_placon_full_sensor():
    w = wd.World()
    w.spawn_box(on_surface(wd.PLACON_X0 + 28, wd.Y_TOP))
    ins = run(w, Outputs(), 30)
    assert ins.I_Placon_Full, "I_Placon_Full should see the box"
    print("  I_Placon_Full fires for box at placon left end OK")


def test_lift_a_travel_and_limits():
    w = wd.World()
    ins = run(w, Outputs(), 5)
    assert ins.I_LftA_Up and not ins.I_LftA_Down, "lift A starts at top"
    ins = run(w, Outputs(Q_LftA_Down=True), 240)   # 190px @ 90px/s ~ 2.1s
    assert ins.I_LftA_Down and not ins.I_LftA_Up, "lift A should reach bottom limit"
    y = w.lift_a.body.position.y
    ins = run(w, Outputs(Q_LftA_Down=True), 30)
    assert abs(w.lift_a.body.position.y - y) < 0.01, "lift A must stop at limit"
    ins = run(w, Outputs(Q_LftA_Up=True), 240)
    assert ins.I_LftA_Up, "lift A should return to top limit"
    print("  lift A travel + both limit switches OK")


def test_lift_b_belt_carries_box_to_end_sensor():
    w = wd.World()
    b = w.spawn_box(on_surface((wd.LIFT_B_X0 + wd.LIFT_B_X1) / 2, wd.Y_TOP))
    run(w, Outputs(), 30)
    ins = run(w, Outputs(Q_LftB_Fwd=True), 150)
    assert b.position.x > wd.LIFT_B_X1 - 80, "box should ride belt forward (+x)"
    assert ins.I_LftB_BeltEnd, "I_LftB_BeltEnd should fire at right end"
    print("  lift B belt fwd carries box to I_LftB_BeltEnd OK")


def test_lift_b_carries_box_down_and_discharges():
    w = wd.World()
    b = w.spawn_box(on_surface(wd.LIFT_B_X0 + 60, wd.Y_TOP))
    run(w, Outputs(), 30)
    ins = run(w, Outputs(Q_LftB_Down=True), 240)
    assert ins.I_LftB_Down, "lift B should be at bottom"
    assert b.position.y > wd.Y_BOT - wd.BOX_H, "box should ride the lift down"
    assert wd.LIFT_B_X0 < b.position.x < wd.LIFT_B_X1, "box stays on lift"
    ins = run(w, Outputs(Q_LftB_Rev=True, Q_Conv_Run=True), 200)
    assert b.position.x < wd.LIFT_B_X0, "box should discharge onto long conveyor"
    print("  lift B carries box down and discharges to long conveyor OK")


def test_spawn_lands_on_placon():
    w = wd.World()
    b = w.spawn_box()
    run(w, Outputs(), 120)
    assert abs(b.position.y - (wd.Y_TOP - wd.BOX_H / 2)) < 3, \
        f"spawned box should rest on placon, y={b.position.y:.1f}"
    print("  spawned box lands on placon OK")


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items()
                            if k.startswith("test_")}.items()):
        fn()
    print("all physics tests passed")
