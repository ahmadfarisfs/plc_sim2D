"""Physics world for the conveyor/lifter rig.

Built on pymunk. Coordinates match pygame: origin top-left, y grows downward,
1 unit = 1 pixel. Belts drive boxes through friction (surface_velocity),
lifts are kinematic platforms travelling between an upper and lower limit.
"""
import pymunk

from .iomap import Inputs, Outputs

# --- geometry (pixels) ---
W, H = 1500, 620           # world drawing area (HUD sits below in the window)
Y_TOP = 280.0              # top-level belt surface (placon line)
Y_BOT = 470.0              # bottom-level belt surface (long conveyor)
BELT_H = 14.0
FLOOR_Y = 580.0            # catches boxes that fall off (PLC logic errors)

LIFT_A_X0, LIFT_A_X1 = 60.0, 280.0
PLACON_X0, PLACON_X1 = 280.0, 1180.0
LONG_X0, LONG_X1 = 280.0, 1180.0
LIFT_B_X0, LIFT_B_X1 = 1180.0, 1400.0

LIFT_SPEED = 90.0          # px/s vertical travel
BELT_SPEED = 130.0         # px/s lift belt surface speed
LONG_SPEED = 130.0         # px/s long conveyor, fixed direction: leftward only

BOX_W, BOX_H = 200.0, 30.0
BOX_RADIUS = 6.0           # rounded corners: avoids snagging on belt seams
BOX_MASS = 1.0
SPAWN_POS = (1060.0, 140.0)
MAX_BOXES = 15

CAT_BOX = 0b1
CAT_FRAME = 0b10
BOX_FILTER = pymunk.ShapeFilter(mask=CAT_BOX)
FRAME_FILTER = pymunk.ShapeFilter(categories=CAT_FRAME)
SENSOR_RANGE = 2.0
LIMIT_TOL = 1.0

# machine-frame end stops at the outer (dead) ends of both lifts
WALL_A_X = LIFT_A_X0 - 6.0
WALL_B_X = LIFT_B_X1 + 6.0
WALL_Y0 = Y_TOP - 70.0


class Lift:
    """Kinematic belt platform travelling between the two levels."""

    def __init__(self, space, x0, x1):
        self.x0, self.x1 = x0, x1
        self.y_hi = Y_TOP + BELT_H / 2     # body center at upper limit
        self.y_lo = Y_BOT + BELT_H / 2     # body center at lower limit
        self.body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        self.body.position = ((x0 + x1) / 2, self.y_hi)
        self.shape = pymunk.Poly.create_box(self.body, (x1 - x0, BELT_H))
        self.shape.friction = 0.9
        self.shape.filter = FRAME_FILTER
        space.add(self.body, self.shape)

    @property
    def at_top(self):
        return abs(self.body.position.y - self.y_hi) < LIMIT_TOL

    @property
    def at_bottom(self):
        return abs(self.body.position.y - self.y_lo) < LIMIT_TOL

    @property
    def surface_y(self):
        return self.body.position.y - BELT_H / 2

    @property
    def belt_dir(self):
        return self.shape.surface_velocity.x

    def drive(self, up, down, fwd, rev, dt):
        y = self.body.position.y
        vy = 0.0
        if up and not down and y > self.y_hi:
            vy = -LIFT_SPEED
            if y + vy * dt < self.y_hi:        # land exactly on the limit
                vy = (self.y_hi - y) / dt
        elif down and not up and y < self.y_lo:
            vy = LIFT_SPEED
            if y + vy * dt > self.y_lo:
                vy = (self.y_lo - y) / dt
        self.body.velocity = (0.0, vy)

        sv = 0.0
        if fwd and not rev:
            sv = -BELT_SPEED
        elif rev and not fwd:
            sv = BELT_SPEED
        self.shape.surface_velocity = (sv, 0.0)


class Sensor:
    """Diffuse proximity sensor: active while any box overlaps its point."""

    def __init__(self, name, pos_fn):
        self.name = name
        self.pos_fn = pos_fn
        self.active = False

    @property
    def pos(self):
        return self.pos_fn()

    def update(self, space):
        self.active = space.point_query_nearest(
            self.pos_fn(), SENSOR_RANGE, BOX_FILTER) is not None


class World:
    def __init__(self, long_speed=LONG_SPEED):
        self.space = pymunk.Space()
        self.space.gravity = (0.0, 900.0)
        self.boxes = []
        self.long_speed = long_speed

        # placon line: undriven, boxes only move when pushed with the mouse
        self.placon = self._belt(PLACON_X0, PLACON_X1, Y_TOP, friction=0.55)
        self.long = self._belt(LONG_X0, LONG_X1, Y_BOT, friction=0.9)
        self.floor = self._belt(0.0, W, FLOOR_Y, friction=0.9)
        for x in (WALL_A_X, WALL_B_X):
            s = pymunk.Segment(self.space.static_body,
                               (x, WALL_Y0), (x, FLOOR_Y), 3.0)
            s.friction = 0.3
            s.filter = FRAME_FILTER
            self.space.add(s)

        self.lift_a = Lift(self.space, LIFT_A_X0, LIFT_A_X1)
        self.lift_b = Lift(self.space, LIFT_B_X0, LIFT_B_X1)

        above = 10.0   # sensing point sits just above the belt surface
        self.sensors = [
            Sensor("I_LftA_BeltEnd",
                   lambda: (LIFT_A_X0 + 22, self.lift_a.surface_y - above)),
            Sensor("I_Placon_Full", lambda: (PLACON_X0 + 28, Y_TOP - above)),
            Sensor("I_Conv_BeltEnd", lambda: (LONG_X0 + 50, Y_BOT - above)),
            Sensor("I_LftB_BeltStart",
                   lambda: (LIFT_B_X0 + 22, self.lift_b.surface_y - above)),
            Sensor("I_LftB_BeltEnd",
                   lambda: (LIFT_B_X1 - 22, self.lift_b.surface_y - above)),
        ]

        self.mouse_body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        self.mouse_joint = None

    def _belt(self, x0, x1, y_surface, friction):
        s = pymunk.Poly(self.space.static_body,
                        [(x0, y_surface), (x1, y_surface),
                         (x1, y_surface + BELT_H), (x0, y_surface + BELT_H)])
        s.friction = friction
        s.filter = FRAME_FILTER
        self.space.add(s)
        return s

    # --- boxes ---
    def spawn_box(self, pos=SPAWN_POS):
        if len(self.boxes) >= MAX_BOXES:
            return None
        body = pymunk.Body(BOX_MASS, pymunk.moment_for_box(BOX_MASS, (BOX_W, BOX_H)))
        body.position = pos
        shape = pymunk.Poly.create_box(
            body, (BOX_W - 2 * BOX_RADIUS, BOX_H - 2 * BOX_RADIUS), radius=BOX_RADIUS)
        shape.friction = 0.8
        shape.filter = pymunk.ShapeFilter(categories=CAT_BOX)
        self.space.add(body, shape)
        self.boxes.append(body)
        return body

    def clear_boxes(self):
        self.release()
        for b in self.boxes:
            self.space.remove(b, *b.shapes)
        self.boxes.clear()

    # --- mouse nudging ---
    def grab(self, p):
        self.release()
        hit = self.space.point_query_nearest(p, 8.0, BOX_FILTER)
        if hit is None or hit.shape is None \
                or hit.shape.body.body_type != pymunk.Body.DYNAMIC:
            return False
        self.mouse_body.position = p
        j = pymunk.PivotJoint(self.mouse_body, hit.shape.body, (0, 0),
                              hit.shape.body.world_to_local(p))
        j.max_force = 60000.0      # firm drag, but boxes can't be teleported
        j.error_bias = (1.0 - 0.15) ** 60.0
        self.space.add(j)
        self.mouse_joint = j
        return True

    def move_mouse(self, p):
        self.mouse_body.position = p

    def release(self):
        if self.mouse_joint is not None:
            self.space.remove(self.mouse_joint)
            self.mouse_joint = None

    # --- simulation step ---
    def update(self, out: Outputs, dt: float, substeps: int = 2) -> Inputs:
        self.lift_a.drive(out.O_LftA_LiftUp, out.O_LftA_LiftDown,
                          out.O_LftA_BeltFwd, out.O_LftA_BeltRev, dt)
        self.lift_b.drive(out.O_LftB_LiftUp, out.O_LftB_LiftDown,
                          out.O_LftB_BeltFwd, out.O_LftB_BeltRev, dt)
        # long conveyor is single-direction by construction (leftward)
        self.long.surface_velocity = \
            (-self.long_speed if out.O_Conv_BeltFwd else 0.0, 0.0)

        sub = dt / substeps
        for _ in range(substeps):
            self.space.step(sub)

        for b in [b for b in self.boxes if b.position.y > H + 200]:
            if self.mouse_joint is not None and self.mouse_joint.b is b:
                self.release()
            self.space.remove(b, *b.shapes)
            self.boxes.remove(b)

        for s in self.sensors:
            s.update(self.space)

        ins = Inputs()
        for s in self.sensors:
            setattr(ins, s.name, s.active)
        ins.I_LftA_UpLimit = self.lift_a.at_top
        ins.I_LftA_DnLimit = self.lift_a.at_bottom
        ins.I_LftB_UpLimit = self.lift_b.at_top
        ins.I_LftB_DnLimit = self.lift_b.at_bottom
        return ins
