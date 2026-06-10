"""Conveyor/lifter rig simulator with Modbus TCP PLC coupling.

Run:  python -m conveyor_sim.main [--mode server|client|none] ...
See README.md for the full I/O map and PLC wiring notes.
"""
import argparse
import sys

import pygame

from . import world as wd
from .iomap import IN_NAMES, OUT_NAMES, Outputs
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
    pygame.K_1: "Q_LftA_Up", pygame.K_2: "Q_LftA_Down",
    pygame.K_3: "Q_LftA_Fwd", pygame.K_4: "Q_LftA_Rev",
    pygame.K_5: "Q_Conv_Run",
    pygame.K_6: "Q_LftB_Up", pygame.K_7: "Q_LftB_Down",
    pygame.K_8: "Q_LftB_Fwd", pygame.K_9: "Q_LftB_Rev",
    pygame.K_0: "Q_Lamp_Ready",
}
OPPOSITE = {
    "Q_LftA_Up": "Q_LftA_Down", "Q_LftA_Down": "Q_LftA_Up",
    "Q_LftA_Fwd": "Q_LftA_Rev", "Q_LftA_Rev": "Q_LftA_Fwd",
    "Q_LftB_Up": "Q_LftB_Down", "Q_LftB_Down": "Q_LftB_Up",
    "Q_LftB_Fwd": "Q_LftB_Rev", "Q_LftB_Rev": "Q_LftB_Fwd",
}


class Panel:
    """Operator panel: latching E-stop (NC), two momentary PBs, ready lamp.

    No behavior lives here — the contacts/lamp are raw I/O for the PLC.
    """

    def __init__(self, x, y):
        self.rect = pygame.Rect(x, y, 330, 130)
        self.estop_pressed = False    # mushroom latched in (click toggles)
        self.pb_start = False         # True while the mouse holds the button
        self.pb_seq = False
        cy = y + 62
        self.estop_c = (x + 55, cy)
        self.start_c = (x + 140, cy)
        self.seq_c = (x + 225, cy)
        self.led_c = (x + 295, cy)

    @staticmethod
    def _hit(p, c, r):
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


def draw_panel(surf, panel, lamp_on, f12):
    pygame.draw.rect(surf, (232, 233, 238), panel.rect, border_radius=6)
    pygame.draw.rect(surf, (150, 150, 160), panel.rect, 2, border_radius=6)
    surf.blit(f12.render("OPERATOR PANEL (click)", True, TXT),
              (panel.rect.x + 10, panel.rect.y + 8))

    def label(c, lines, color=TXT):
        for i, ln in enumerate(lines):
            t = f12.render(ln, True, color)
            surf.blit(t, (c[0] - t.get_width() // 2, c[1] + 30 + 13 * i))

    # E-stop: yellow base, red mushroom, latches in when clicked
    x, y = panel.estop_c
    pygame.draw.circle(surf, (240, 200, 40), (x, y), 26)
    r = 17 if panel.estop_pressed else 21
    pygame.draw.circle(surf, (160, 20, 20) if panel.estop_pressed else (215, 40, 40),
                       (x, y), r)
    label(panel.estop_c, ["E-STOP", "PRESSED" if panel.estop_pressed else "(NC)"],
          (180, 30, 30) if panel.estop_pressed else TXT)

    for c, held, color in ((panel.start_c, panel.pb_start, (40, 160, 70)),
                           (panel.seq_c, panel.pb_seq, (40, 110, 200))):
        pygame.draw.circle(surf, (120, 120, 130), c, 20)
        shade = tuple(int(v * 0.6) for v in color) if held else color
        pygame.draw.circle(surf, shade, c, 16 if held else 18)
    label(panel.start_c, ["START", "MACHINE"])
    label(panel.seq_c, ["START", "SEQUENCE"])

    x, y = panel.led_c
    pygame.draw.circle(surf, (60, 220, 90) if lamp_on else (180, 185, 190), (x, y), 11)
    pygame.draw.circle(surf, (110, 110, 120), (x, y), 11, 2)
    if lamp_on:
        pygame.draw.circle(surf, (60, 220, 90), (x, y), 16, 2)
    label(panel.led_c, ["READY"])


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Conveyor rig physics sim + Modbus TCP")
    p.add_argument("--mode", choices=["server", "client", "none"], default="server",
                   help="server: sim is Modbus slave, PLC polls it (default). "
                        "client: sim polls the PLC's Modbus server. "
                        "none: manual keyboard control only")
    p.add_argument("--host", default=None,
                   help="bind address (server, default 0.0.0.0) or PLC IP (client)")
    p.add_argument("--port", type=int, default=None,
                   help="TCP port (default: 5020 server / 502 client)")
    p.add_argument("--device-id", type=int, default=1, help="Modbus unit/device id (client mode)")
    p.add_argument("--in-base", type=int, default=0,
                   help="client mode: PLC coil address where sensor bits are written")
    p.add_argument("--out-base", type=int, default=16,
                   help="client mode: PLC coil address where actuator commands are read")
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


def draw_belt_rect(surf, x0, x1, y, running=False):
    r = pygame.Rect(x0, y, x1 - x0, wd.BELT_H)
    pygame.draw.rect(surf, BELT_RUN if running else BELT_FILL, r)
    pygame.draw.rect(surf, BELT_EDGE, r, 1)
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
    for b in world.boxes:
        for s in b.shapes:
            pts = [b.local_to_world(v) for v in s.get_vertices()]
            pygame.draw.polygon(surf, BOX_FILL, pts)
            pygame.draw.polygon(surf, BOX_EDGE, pts, 2)

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


def draw_hud(surf, fonts, ins, outs, link_status, manual, n_boxes):
    f12, f14 = fonts
    pygame.draw.rect(surf, (238, 239, 243), pygame.Rect(0, HUD_Y, WIN_W, WIN_H - HUD_Y))
    pygame.draw.line(surf, (200, 200, 205), (0, HUD_Y), (WIN_W, HUD_Y), 1)

    src = "control: MANUAL (keyboard)" if manual else "control: PLC"
    surf.blit(f14.render(f"{link_status}   |   {src}   |   boxes: {n_boxes}",
                         True, WARN if manual else TXT), (20, HUD_Y + 10))
    surf.blit(f14.render(
        "SPACE spawn | right-click spawn at mouse | left-drag nudge | "
        "C clear boxes | M toggle manual/PLC | ESC quit", True, DIM),
        (20, HUD_Y + 32))
    surf.blit(f14.render(
        "manual keys:  1/2 LiftA up/down   3/4 LiftA belt fwd/rev   "
        "5 long conveyor   6/7 LiftB up/down   8/9 LiftB belt fwd/rev   "
        "0 ready lamp",
        True, DIM if not manual else TXT), (20, HUD_Y + 54))

    def column(x, title, names, values, addr_label):
        surf.blit(f14.render(title, True, TXT), (x, HUD_Y + 84))
        for i, (n, v) in enumerate(zip(names, values)):
            y = HUD_Y + 108 + i * 14
            pygame.draw.circle(surf, OK_GREEN if v else (205, 205, 210), (x + 6, y + 6), 5)
            surf.blit(f12.render(f"{addr_label}{i}  {n}", True, TXT if v else DIM),
                      (x + 18, y))

    column(20, "sensors -> PLC (discrete inputs, FC02)", IN_NAMES, ins.to_bits(), "DI ")
    column(560, "PLC -> actuators (coils, FC01/05/15)", OUT_NAMES, outs.to_bits(), "C ")


def main(argv=None):
    args = parse_args(argv)
    link = make_link(args)

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("conveyor rig sim - Modbus TCP")
    clock = pygame.time.Clock()
    f12 = pygame.font.SysFont("menlo, monaco, consolas, monospace", 12)
    f14 = pygame.font.SysFont("menlo, monaco, consolas, monospace", 14)

    world = wd.World()
    panel = Panel(40, 36)
    manual = args.mode == "none"
    manual_out = Outputs()
    dt = 1.0 / 60.0
    frame = 0
    running = True

    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
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
                if not panel.mouse_down(e.pos):
                    world.grab(e.pos)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
                world.spawn_box(e.pos)
            elif e.type == pygame.MOUSEMOTION:
                world.move_mouse(e.pos)
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                panel.mouse_up()
                world.release()

        if manual:
            outs = manual_out
        else:
            outs = Outputs.from_bits(link.pull_outputs())

        ins = world.update(outs, dt)
        ins.I_EStop_NC = not panel.estop_pressed   # NC: contact opens when pressed
        ins.I_PB_Start = panel.pb_start
        ins.I_PB_Seq = panel.pb_seq
        link.push_inputs(ins.to_bits())

        draw_world(screen, world, f12)
        draw_panel(screen, panel, outs.Q_Lamp_Ready, f12)
        draw_hud(screen, (f12, f14), ins, outs, link.status, manual, len(world.boxes))
        pygame.display.flip()
        clock.tick(60)

        frame += 1
        if args.frames and frame >= args.frames:
            if args.screenshot:
                pygame.image.save(screen, args.screenshot)
            running = False

    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
