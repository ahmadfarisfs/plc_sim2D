"""Conveyor/lifter rig simulator with Modbus TCP PLC coupling.

Run:  python -m conveyor_sim.main [--mode server|client|none] ...
See README.md for the full I/O map and PLC wiring notes.
"""
import argparse
import math
import sys

import pygame

from . import world as wd
from .iomap import FX5U_X_DEVICES, IN_NAMES, MC_M_BASE, MC_Y_DEVICES, OUT_NAMES, Outputs
from .plc import make_link

WIN_W, WIN_H = wd.W, 900
HUD_Y = wd.H

BG = (248, 248, 250)
BELT_FILL = (228, 228, 230)
BELT_EDGE = (90, 90, 95)
BELT_RUN = (200, 232, 200)
LIFT_FILL = (215, 220, 232)
BOX_FILL = (174, 203, 242)
BOX_EDGE = (70, 110, 180)
SENSOR_OFF = (255, 255, 255)
SENSOR_ON = (235, 60, 60)
SENSOR_EDGE = (200, 60, 60)
TXT = (40, 40, 45)
DIM = (120, 120, 130)
OK_GREEN = (40, 160, 70)
WARN = (200, 120, 30)

# manual-mode key bindings (toggles)
KEYMAP = {
    pygame.K_1: "O_LftA_LiftUp", pygame.K_2: "O_LftA_LiftDown",
    pygame.K_3: "O_LftA_BeltFwd", pygame.K_4: "O_LftA_BeltRev",
    pygame.K_5: "O_Conv_BeltFwd",
    pygame.K_6: "O_LftB_LiftUp", pygame.K_7: "O_LftB_LiftDown",
    pygame.K_8: "O_LftB_BeltFwd", pygame.K_9: "O_LftB_BeltRev",
    pygame.K_0: "O_Pnl_MachineReady",
    pygame.K_MINUS: "O_Pnl_Alarm",
}
OPPOSITE = {
    "O_LftA_LiftUp": "O_LftA_LiftDown", "O_LftA_LiftDown": "O_LftA_LiftUp",
    "O_LftA_BeltFwd": "O_LftA_BeltRev", "O_LftA_BeltRev": "O_LftA_BeltFwd",
    "O_LftB_LiftUp": "O_LftB_LiftDown", "O_LftB_LiftDown": "O_LftB_LiftUp",
    "O_LftB_BeltFwd": "O_LftB_BeltRev", "O_LftB_BeltRev": "O_LftB_BeltFwd",
}


PANEL_W, PANEL_H = 400, 130    # native size of the panel artwork
PANEL_SCALE = 0.5               # panel is drawn at this fraction of native size


class Panel:
    """Operator panel: latching E-stop (NC), two momentary PBs, ready lamp.

    No behavior lives here — the contacts/lamp are raw I/O for the PLC.
    Drawn at PANEL_SCALE of its native size; hit-test geometry below is
    scaled to match.
    """

    def __init__(self, x, y):
        self.rect = pygame.Rect(x, y, int(PANEL_W * PANEL_SCALE), int(PANEL_H * PANEL_SCALE))
        self.estop_pressed = False    # mushroom latched in (click toggles)
        self.pb_start = False         # True while the mouse holds the button
        self.pb_seq = False
        cy = y + 62 * PANEL_SCALE
        self.estop_c = (x + 55 * PANEL_SCALE, cy)
        self.start_c = (x + 140 * PANEL_SCALE, cy)
        self.seq_c = (x + 225 * PANEL_SCALE, cy)
        self.led_c = (x + 295 * PANEL_SCALE, cy)
        self.alarm_c = (x + 360 * PANEL_SCALE, cy)

    @staticmethod
    def _hit(p, c, r):
        r *= PANEL_SCALE
        return (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 <= r * r

    def mouse_down(self, p):
        """Returns True if the click was consumed by the panel."""
        if self._hit(p, self.estop_c, 26):
            self.estop_pressed = not self.estop_pressed
            return True
        if self._hit(p, self.start_c, 20):
            self.pb_start = True
            return True
        if self._hit(p, self.seq_c, 20):
            self.pb_seq = True
            return True
        return self.rect.collidepoint(p)

    def mouse_up(self):
        self.pb_start = False
        self.pb_seq = False


def draw_panel(surf, panel, ready_on, alarm_on, f12):
    """Draw the operator panel artwork at native size, then scale it down
    by PANEL_SCALE onto surf (see Panel.__init__ for the matching geometry).
    """
    tmp = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    rect0 = tmp.get_rect()
    pygame.draw.rect(tmp, (232, 233, 238), rect0, border_radius=6)
    pygame.draw.rect(tmp, (150, 150, 160), rect0, 2, border_radius=6)
    tmp.blit(f12.render("OPERATOR PANEL (click)", True, TXT), (10, 8))

    cy = 62
    estop_c, start_c, seq_c, led_c, alarm_c = \
        (55, cy), (140, cy), (225, cy), (295, cy), (360, cy)

    def label(c, lines, color=TXT):
        for i, ln in enumerate(lines):
            t = f12.render(ln, True, color)
            tmp.blit(t, (c[0] - t.get_width() // 2, c[1] + 30 + 13 * i))

    # E-stop: yellow base, red mushroom, latches in when clicked
    x, y = estop_c
    pygame.draw.circle(tmp, (240, 200, 40), (x, y), 26)
    r = 17 if panel.estop_pressed else 21
    pygame.draw.circle(tmp, (160, 20, 20) if panel.estop_pressed else (215, 40, 40),
                       (x, y), r)
    label(estop_c, ["E-STOP", "PRESSED" if panel.estop_pressed else "(NC)"],
          (180, 30, 30) if panel.estop_pressed else TXT)

    for c, held, color in ((start_c, panel.pb_start, (40, 160, 70)),
                           (seq_c, panel.pb_seq, (40, 110, 200))):
        pygame.draw.circle(tmp, (120, 120, 130), c, 20)
        shade = tuple(int(v * 0.6) for v in color) if held else color
        pygame.draw.circle(tmp, shade, c, 16 if held else 18)
    label(start_c, ["START", "MACHINE"])
    label(seq_c, ["START", "SEQUENCE"])

    for c, on, color, name in ((led_c, ready_on, (60, 220, 90), "READY"),
                               (alarm_c, alarm_on, (235, 60, 60), "ALARM")):
        x, y = c
        pygame.draw.circle(tmp, color if on else (180, 185, 190), (x, y), 11)
        pygame.draw.circle(tmp, (110, 110, 120), (x, y), 11, 2)
        if on:
            pygame.draw.circle(tmp, color, (x, y), 16, 2)
        label(c, [name], color if on else TXT)

    surf.blit(pygame.transform.smoothscale(tmp, panel.rect.size), panel.rect.topleft)


class EStopButton:
    """Standalone latching E-stop (NC contact) mounted near a lift.

    Click to latch/release, like the panel's E-stop but independent of it.
    """

    def __init__(self, x, y, label):
        self.pos = (x, y)
        self.label = label
        self.pressed = False

    def mouse_down(self, p):
        x, y = self.pos
        if (p[0] - x) ** 2 + (p[1] - y) ** 2 <= 26 ** 2:
            self.pressed = not self.pressed
            return True
        return False


def draw_estop(surf, estop, f12):
    x, y = estop.pos
    pygame.draw.circle(surf, (240, 200, 40), (x, y), 26)
    r = 17 if estop.pressed else 21
    pygame.draw.circle(surf, (160, 20, 20) if estop.pressed else (215, 40, 40), (x, y), r)
    color = (180, 30, 30) if estop.pressed else TXT
    for i, ln in enumerate((estop.label, "PRESSED" if estop.pressed else "(NC)")):
        t = f12.render(ln, True, color)
        surf.blit(t, (x - t.get_width() // 2, y + 30 + 13 * i))


class Button:
    """Small on-screen button: toggles an output signal or runs an action."""

    def __init__(self, x, y, w, label, signal=None, action=None):
        self.rect = pygame.Rect(x, y, w, 22)
        self.label = label
        self.signal = signal     # output name on manual_out (toggle)
        self.action = action     # "spawn" / "clear" (momentary)


def make_buttons():
    y = wd.FLOOR_Y + 10
    btns = []

    def strip(x, items, w=50, gap=4):
        for label, sig in items:
            btns.append(Button(x, y, w, label, signal=sig))
            x += w + gap

    strip(wd.LIFT_A_X0, [("A up", "O_LftA_LiftUp"), ("A dn", "O_LftA_LiftDown"),
                         ("A fwd", "O_LftA_BeltFwd"), ("A rev", "O_LftA_BeltRev")])
    strip(560, [("conv run", "O_Conv_BeltFwd")], w=82)
    strip(656, [("ready", "O_Pnl_MachineReady"), ("alarm", "O_Pnl_Alarm")], w=56)
    btns.append(Button(880, y, 60, "spawn", action="spawn"))
    btns.append(Button(944, y, 60, "clear", action="clear"))
    strip(wd.LIFT_B_X0 - 16, [("B up", "O_LftB_LiftUp"), ("B dn", "O_LftB_LiftDown"),
                              ("B fwd", "O_LftB_BeltFwd"), ("B rev", "O_LftB_BeltRev")])
    return btns


def buttons_click(buttons, pos, world, manual_out):
    """Returns 'toggled' if an output button was used, True if any button hit."""
    for b in buttons:
        if not b.rect.collidepoint(pos):
            continue
        if b.action == "spawn":
            world.spawn_box()
        elif b.action == "clear":
            world.clear_boxes()
        else:
            val = not getattr(manual_out, b.signal)
            setattr(manual_out, b.signal, val)
            if val and b.signal in OPPOSITE:
                setattr(manual_out, OPPOSITE[b.signal], False)
            return "toggled"
        return True
    return False


def draw_buttons(surf, buttons, outs, f12):
    for b in buttons:
        on = b.signal is not None and getattr(outs, b.signal)
        fill = (140, 215, 160) if on else (224, 225, 230)
        pygame.draw.rect(surf, fill, b.rect, border_radius=4)
        pygame.draw.rect(surf, (130, 130, 140), b.rect, 1, border_radius=4)
        t = f12.render(b.label, True, TXT)
        surf.blit(t, (b.rect.centerx - t.get_width() // 2,
                      b.rect.centery - t.get_height() // 2))


class Slider:
    """Horizontal slider: drag the handle to set a float value in [vmin, vmax]."""

    def __init__(self, x, y, w, vmin, vmax, value, label, fmt="{:.0f}"):
        self.rect = pygame.Rect(x, y, w, 16)
        self.vmin, self.vmax = vmin, vmax
        self.value = value
        self.label = label
        self.fmt = fmt
        self.dragging = False

    def _value_at(self, px):
        frac = (px - self.rect.x) / self.rect.w
        frac = max(0.0, min(1.0, frac))
        return self.vmin + frac * (self.vmax - self.vmin)

    def mouse_down(self, p):
        if self.rect.inflate(0, 12).collidepoint(p):
            self.dragging = True
            self.value = self._value_at(p[0])
            return True
        return False

    def mouse_motion(self, p):
        if self.dragging:
            self.value = self._value_at(p[0])

    def mouse_up(self):
        self.dragging = False


def draw_slider(surf, slider, f12):
    text = f"{slider.label}: {slider.fmt.format(slider.value)} px/s"
    surf.blit(f12.render(text, True, TXT), (slider.rect.x, slider.rect.y - 16))
    pygame.draw.rect(surf, (224, 225, 230), slider.rect, border_radius=8)
    pygame.draw.rect(surf, (130, 130, 140), slider.rect, 1, border_radius=8)
    frac = (slider.value - slider.vmin) / (slider.vmax - slider.vmin)
    hx = slider.rect.x + int(frac * slider.rect.w)
    pygame.draw.circle(surf, (80, 120, 200), (hx, slider.rect.centery), 9)
    pygame.draw.circle(surf, (50, 80, 150), (hx, slider.rect.centery), 9, 1)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Conveyor rig physics sim + Modbus TCP / Mitsubishi MC protocol")
    p.add_argument("--mode", choices=["server", "client", "mc", "soft", "none"],
                   default="server",
                   help="server: sim is Modbus slave, PLC polls it (default). "
                        "client: sim polls the PLC's Modbus server. "
                        "mc: Mitsubishi MC protocol, sensors -> M200.., cmds <- Y. "
                        "soft: run a GX Works3 XML export in-process, no PLC; "
                        "sensors -> X devices, cmds <- Y, like real FX5U wiring. "
                        "none: manual keyboard control only")
    p.add_argument("--plc-xml", default=None,
                   help="soft mode: path to the GX Works3 PLCopen XML export")
    p.add_argument("--host", default=None,
                   help="bind address (server, default 0.0.0.0) or PLC IP (client/mc)")
    p.add_argument("--port", type=int, default=None,
                   help="TCP port (default: 5020 server / 502 client / 5007 mc)")
    p.add_argument("--device-id", type=int, default=1, help="Modbus unit/device id (client mode)")
    p.add_argument("--in-base", type=int, default=0,
                   help="client mode: PLC coil address where sensor bits are written")
    p.add_argument("--out-base", type=int, default=16,
                   help="client mode: PLC coil address where actuator commands are read")
    p.add_argument("--plctype", choices=["Q", "L", "QnA", "iQ-L", "iQ-R"],
                   default="Q", help="mc mode: pymcprotocol PLC series (default Q)")
    p.add_argument("--m-base", type=int, default=200,
                   help="mc mode: first M device for sensor bits (default M200)")
    p.add_argument("--y-radix", choices=["oct", "hex"], default="oct",
                   help="mc mode: Y device numbering (FX is octal: Y7 -> Y10)")
    p.add_argument("--long-speed", type=float, default=wd.LONG_SPEED,
                   help=f"long conveyor belt speed, px/s (default {wd.LONG_SPEED})")
    p.add_argument("--frames", type=int, default=0,
                   help="exit after N frames (testing)")
    p.add_argument("--screenshot", default=None,
                   help="save a PNG of the last frame (testing)")
    return p.parse_args(argv)


def draw_arrow(surf, color, a, b, w=3):
    import math
    pygame.draw.line(surf, color, a, b, w)
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    for da in (2.6, -2.6):
        tip = (b[0] + 12 * math.cos(ang + da), b[1] + 12 * math.sin(ang + da))
        pygame.draw.line(surf, color, b, tip, w)


_BOX_SURF = None


def _box_surface():
    global _BOX_SURF
    if _BOX_SURF is None:
        w, h = int(wd.BOX_W), int(wd.BOX_H)
        radius = int(wd.BOX_RADIUS)
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        rect = pygame.Rect(0, 0, w, h)
        pygame.draw.rect(s, BOX_FILL, rect, border_radius=radius)
        pygame.draw.rect(s, BOX_EDGE, rect, 2, border_radius=radius)
        _BOX_SURF = s
    return _BOX_SURF


def draw_belt_rect(surf, x0, x1, y, running=False):
    r = pygame.Rect(x0, y, x1 - x0, wd.BELT_H)
    radius = int(wd.BELT_H / 2)
    pygame.draw.rect(surf, BELT_RUN if running else BELT_FILL, r, border_radius=radius)
    pygame.draw.rect(surf, BELT_EDGE, r, 1, border_radius=radius)
    return r


def draw_world(surf, world, f12):
    surf.fill(BG)

    # placon line (undriven)
    draw_belt_rect(surf, wd.PLACON_X0, wd.PLACON_X1, wd.Y_TOP)
    surf.blit(f12.render("placon line (manual push)", True, DIM),
              (wd.PLACON_X0 + 330, wd.Y_TOP + wd.BELT_H + 4))

    # long conveyor (one-way, leftward)
    running = world.long.surface_velocity.x != 0
    draw_belt_rect(surf, wd.LONG_X0, wd.LONG_X1, wd.Y_BOT, running)
    surf.blit(f12.render("long conveyor (one-way)", True, DIM),
              (wd.LONG_X0 + 330, wd.Y_BOT + wd.BELT_H + 4))
    draw_arrow(surf, OK_GREEN if running else DIM,
               (wd.LONG_X0 + 560, wd.Y_BOT - 14), (wd.LONG_X0 + 480, wd.Y_BOT - 14))

    # floor and frame end stops
    pygame.draw.line(surf, (200, 200, 205), (0, wd.FLOOR_Y), (wd.W, wd.FLOOR_Y), 2)
    for x in (wd.WALL_A_X, wd.WALL_B_X):
        pygame.draw.line(surf, (150, 150, 160), (x, wd.WALL_Y0), (x, wd.FLOOR_Y), 6)

    # lifts: travel guides, limit marks, belt platform
    for lift, label in ((world.lift_a, "conveyor A (lift)"),
                        (world.lift_b, "conveyor B (lift)")):
        cx = (lift.x0 + lift.x1) / 2
        pygame.draw.line(surf, (210, 210, 215),
                         (cx, wd.Y_TOP - 24), (cx, wd.Y_BOT + wd.BELT_H + 24), 1)
        for yy, at in ((wd.Y_TOP, lift.at_top), (wd.Y_BOT, lift.at_bottom)):
            c = OK_GREEN if at else (190, 190, 195)
            pygame.draw.line(surf, c, (lift.x0 - 6, yy), (lift.x0 - 14, yy), 3)
            pygame.draw.line(surf, c, (lift.x1 + 6, yy), (lift.x1 + 14, yy), 3)
        y = lift.surface_y
        draw_belt_rect(surf, lift.x0, lift.x1, y, lift.belt_dir != 0)
        if lift.belt_dir > 0:
            draw_arrow(surf, OK_GREEN, (cx - 40, y - 10), (cx + 40, y - 10), 2)
        elif lift.belt_dir < 0:
            draw_arrow(surf, OK_GREEN, (cx + 40, y - 10), (cx - 40, y - 10), 2)
        surf.blit(f12.render(label, True, DIM), (lift.x0 + 8, y + wd.BELT_H + 4))

    # boxes
    box_img = _box_surface()
    for b in world.boxes:
        rotated = pygame.transform.rotate(box_img, -math.degrees(b.angle))
        rect = rotated.get_rect(center=b.position)
        surf.blit(rotated, rect)

    # spawn marker
    sx, sy = wd.SPAWN_POS
    draw_arrow(surf, (120, 120, 200), (sx, sy - 60), (sx, sy - 10), 4)
    surf.blit(f12.render("box spawn (SPACE)", True, (120, 120, 200)), (sx - 50, sy - 80))

    # proximity sensors
    for s in world.sensors:
        x, y = s.pos
        if s.active:
            pygame.draw.circle(surf, (255, 170, 170), (x, y), 12)
        pygame.draw.circle(surf, SENSOR_ON if s.active else SENSOR_OFF, (x, y), 7)
        pygame.draw.circle(surf, SENSOR_EDGE, (x, y), 7, 2)
        surf.blit(f12.render(s.name, True, TXT), (x - 30, y - 30))


def draw_jig_counter(surf, jig_count, f12, f14):
    """Digital readout for the retentive jig counter (D2000, M_Jig_Conv_JigCount)."""
    rect = pygame.Rect(WIN_W - 220, HUD_Y + 8, 200, 40)
    pygame.draw.rect(surf, (30, 34, 40), rect, border_radius=6)
    pygame.draw.rect(surf, (90, 95, 105), rect, 1, border_radius=6)
    surf.blit(f12.render("D2000  M_Jig_Conv_JigCount", True, (170, 200, 220)),
              (rect.x + 10, rect.y + 4))
    text = "----" if jig_count is None else f"{jig_count:d}"
    t = f14.render(text, True, (120, 230, 140))
    surf.blit(t, (rect.right - 10 - t.get_width(), rect.y + 18))


def draw_hud(surf, fonts, ins, outs, link_status, manual, n_boxes, in_title, in_addrs,
              jig_count):
    f12, f14 = fonts
    pygame.draw.rect(surf, (238, 239, 243), pygame.Rect(0, HUD_Y, WIN_W, WIN_H - HUD_Y))
    pygame.draw.line(surf, (200, 200, 205), (0, HUD_Y), (WIN_W, HUD_Y), 1)

    src = "control: MANUAL (keyboard)" if manual else "control: PLC"
    surf.blit(f14.render(f"{link_status}   |   {src}   |   boxes: {n_boxes}",
                         True, WARN if manual else TXT), (20, HUD_Y + 10))
    draw_jig_counter(surf, jig_count, f12, f14)
    surf.blit(f14.render(
        "SPACE spawn | right-click spawn at mouse | left-drag nudge | "
        "C clear boxes | M toggle manual/PLC | ESC quit | "
        "click E-STOP A/B mushrooms to latch/release", True, DIM),
        (20, HUD_Y + 32))
    surf.blit(f14.render(
        "manual: click the small buttons, or keys  1/2 LiftA up/down   "
        "3/4 LiftA belt fwd/rev   5 long conveyor   6/7 LiftB up/down   "
        "8/9 LiftB belt fwd/rev   0 ready   - alarm",
        True, DIM if not manual else TXT), (20, HUD_Y + 54))

    def column(x, title, names, values, addrs):
        surf.blit(f14.render(title, True, TXT), (x, HUD_Y + 84))
        for i, (n, v) in enumerate(zip(names, values)):
            y = HUD_Y + 108 + i * 14
            pygame.draw.circle(surf, OK_GREEN if v else (205, 205, 210), (x + 6, y + 6), 5)
            surf.blit(f12.render(f"{addrs[i]:>4}  {n}", True, TXT if v else DIM),
                      (x + 18, y))

    column(20, in_title, IN_NAMES, ins.to_bits(), in_addrs)
    column(560, "PLC -> actuators (Y devices / Modbus coils)", OUT_NAMES,
           outs.to_bits(), MC_Y_DEVICES)


def fit_canvas(screen_size):
    """Scale/offset to fit the fixed-size canvas into the window, letterboxed."""
    sw, sh = screen_size
    scale = max(min(sw / WIN_W, sh / WIN_H), 0.01)
    ox = (sw - WIN_W * scale) / 2
    oy = (sh - WIN_H * scale) / 2
    return scale, ox, oy


def to_canvas(pos, scale, ox, oy):
    return ((pos[0] - ox) / scale, (pos[1] - oy) / scale)


def main(argv=None):
    args = parse_args(argv)
    link = make_link(args)

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
    pygame.display.set_caption("conveyor rig sim - Modbus TCP")
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.ShowWindow(pygame.display.get_wm_info()["window"], 3)  # SW_MAXIMIZE
    canvas = pygame.Surface((WIN_W, WIN_H))
    clock = pygame.time.Clock()
    f12 = pygame.font.SysFont("menlo, monaco, consolas, monospace", 12)
    f14 = pygame.font.SysFont("menlo, monaco, consolas, monospace", 14)

    world = wd.World(long_speed=args.long_speed)
    panel = Panel(40, 36)
    estop_a = EStopButton((wd.LIFT_A_X0 + wd.LIFT_A_X1) / 2, 160, "E-STOP A")
    estop_b = EStopButton((wd.LIFT_B_X0 + wd.LIFT_B_X1) / 2, 160, "E-STOP B")
    buttons = make_buttons()
    speed_slider = Slider(1000, HUD_Y + 108, 300, 0.0, 300.0,
                          args.long_speed, "long conveyor speed")
    manual = args.mode == "none"

    if args.mode == "soft":
        in_title = "sensors -> PLC (X devices, FX5U wiring)"
        in_addrs = [f"{'!' if invert else ''}{dev}" for dev, invert in FX5U_X_DEVICES]
    else:
        in_title = "sensors -> PLC (M devices / Modbus DI)"
        in_addrs = [f"M{MC_M_BASE + i}" for i in range(len(IN_NAMES))]
    manual_out = Outputs()
    dt = 1.0 / 60.0
    frame = 0
    running = True
    scale, ox, oy = fit_canvas(screen.get_size())

    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(e.size, pygame.RESIZABLE)
                scale, ox, oy = fit_canvas(screen.get_size())
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    running = False
                elif e.key == pygame.K_SPACE:
                    world.spawn_box()
                elif e.key == pygame.K_c:
                    world.clear_boxes()
                elif e.key == pygame.K_m:
                    manual = not manual
                elif e.key in KEYMAP and manual:
                    name = KEYMAP[e.key]
                    val = not getattr(manual_out, name)
                    setattr(manual_out, name, val)
                    if val and name in OPPOSITE:
                        setattr(manual_out, OPPOSITE[name], False)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                pos = to_canvas(e.pos, scale, ox, oy)
                if not speed_slider.mouse_down(pos) and not panel.mouse_down(pos) \
                        and not estop_a.mouse_down(pos) and not estop_b.mouse_down(pos):
                    hit = buttons_click(buttons, pos, world, manual_out)
                    if hit == "toggled":
                        manual = True    # motor buttons drive manual control
                    elif not hit:
                        world.grab(pos)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
                world.spawn_box(to_canvas(e.pos, scale, ox, oy))
            elif e.type == pygame.MOUSEMOTION:
                pos = to_canvas(e.pos, scale, ox, oy)
                speed_slider.mouse_motion(pos)
                world.move_mouse(pos)
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                speed_slider.mouse_up()
                panel.mouse_up()
                world.release()

        world.long_speed = speed_slider.value

        if manual:
            outs = manual_out
        else:
            outs = Outputs.from_bits(link.pull_outputs())

        ins = world.update(outs, dt)
        ins.I_Pnl_EStop = not panel.estop_pressed   # NC: contact opens when pressed
        ins.I_Pnl_MachineStartPB = panel.pb_start
        ins.I_Pnl_SeqStartPB = panel.pb_seq
        ins.I_LftA_EmergencyStop = not estop_a.pressed
        ins.I_LftB_EmergencyStop = not estop_b.pressed
        link.push_inputs(ins.to_bits())

        draw_world(canvas, world, f12)
        draw_panel(canvas, panel, outs.O_Pnl_MachineReady, outs.O_Pnl_Alarm, f12)
        draw_estop(canvas, estop_a, f12)
        draw_estop(canvas, estop_b, f12)
        draw_buttons(canvas, buttons, outs, f12)
        draw_hud(canvas, (f12, f14), ins, outs, link.status, manual, len(world.boxes),
                 in_title, in_addrs, getattr(link, "jig_count", None))
        draw_slider(canvas, speed_slider, f12)

        scale, ox, oy = fit_canvas(screen.get_size())
        screen.fill((0, 0, 0))
        scaled = pygame.transform.smoothscale(
            canvas, (round(WIN_W * scale), round(WIN_H * scale)))
        screen.blit(scaled, (ox, oy))
        pygame.display.flip()
        clock.tick(60)

        frame += 1
        if args.frames and frame >= args.frames:
            if args.screenshot:
                pygame.image.save(canvas, args.screenshot)
            running = False

    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
