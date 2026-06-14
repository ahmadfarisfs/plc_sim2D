"""Runtime: ties the XML loader and ST interpreter together into a PLC that
can be stepped one scan at a time."""
from __future__ import annotations

import time

from .project import load_project
from .st_lang import Interpreter, Scope, TimerState, Var, truthy


def _out_t(interp: Interpreter, args: list, scope: Scope, dt: float) -> None:
    """``OUT_T(EN, timer, K<preset>)`` -- TON-style timer. ``K<preset>`` is
    in units of 100ms (matches the program's ``K80`` == 8.0s comments)."""
    if len(args) != 3 or not isinstance(args[1], Var):
        return
    en = truthy(interp.eval(args[0], scope))
    preset_s = interp.eval(args[2], scope) * 0.1
    timer = scope.get_timer(args[1].name)
    if en:
        timer.et = min(timer.et + dt, preset_s)
        timer.s = timer.et >= preset_s
    else:
        timer.et = 0.0
        timer.s = False


def _pls(interp: Interpreter, args: list, scope: Scope, dt: float) -> None:
    """``PLS(IN, OUT)`` -- OUT is TRUE for one scan on the rising edge of IN."""
    if len(args) != 2 or not isinstance(args[1], Var):
        return
    cur = truthy(interp.eval(args[0], scope))
    key = id(args[0])
    prev = interp.pls_prev.get(key, False)
    interp.pls_prev[key] = cur
    scope.set(args[1].name, cur and not prev)


CALL_DISPATCH = {"OUT_T": _out_t, "PLS": _pls}


class SoftPlc:
    """Loads a GX Works3 PLCopen XML export and runs its ST programs.

    Generic: global variables start at whatever initial values the XML
    declares (including ``G_IsSimulated``) and devices (``M``/``X``/``Y``,
    see :meth:`set_device`/:meth:`get_device`) start FALSE. Callers that want
    to emulate a particular PLC's wiring (e.g. forcing ``G_IsSimulated`` to
    match real hardware, or driving specific input devices) do so via
    :meth:`set`/:meth:`set_device` -- this runtime doesn't special-case any
    program variable. The ``initial`` resource (InitVar) runs once at
    construction; each :meth:`step` runs the ``scan`` resource's programs in
    their declared order (SimIO, Safety, FeedLifterA, FeedLifterB).
    """

    def __init__(self, xml_path: str):
        self.project = load_project(xml_path)
        self.globals = dict(self.project.global_vars)
        self.devices: dict[str, bool] = {}

        self._pou_locals: dict[str, dict] = {}
        self._pou_timers: dict[str, dict] = {}
        for name, pou in self.project.pous.items():
            local_ns: dict = {}
            timers: dict = {}
            for var_name, initial in pou.local_vars.items():
                if isinstance(initial, TimerState):
                    timer = TimerState()
                    local_ns[var_name] = timer
                    timers[var_name] = timer
                else:
                    local_ns[var_name] = initial
            self._pou_locals[name] = local_ns
            self._pou_timers[name] = timers

        self.interp = Interpreter(CALL_DISPATCH)
        self._last_time: float | None = None

        for name in self.project.initial_pous:
            self._run_pou(name, 0.0)

    def _run_pou(self, name: str, dt: float) -> None:
        pou = self.project.pous[name]
        scope = Scope(self.globals, self._pou_locals[name], self.devices,
                       self._pou_timers[name])
        for _worksheet, stmts in pou.bodies:
            self.interp.exec_body(stmts, scope, dt)

    def step(self, dt: float | None = None) -> None:
        """Run one scan of the ``scan`` resource's programs.

        ``dt`` is the elapsed time in seconds, used by timer FBs (OUT_T).
        If omitted, it is measured from the wall clock since the previous
        ``step()`` so timers run in real time; pass an explicit ``dt`` to
        fast-forward deterministically (e.g. in tests).
        """
        if dt is None:
            now = time.monotonic()
            dt = 0.0 if self._last_time is None else now - self._last_time
            self._last_time = now
        for name in self.project.scan_pous:
            self._run_pou(name, dt)

    def set_device(self, name: str, value: bool) -> None:
        self.devices[name.upper()] = bool(value)

    def get_device(self, name: str) -> bool:
        return self.devices.get(name.upper(), False)

    def get(self, name: str):
        return self.globals.get(name.lower(), False)

    def set(self, name: str, value) -> None:
        self.globals[name.lower()] = value
