"""Loader for GX Works3 PLCopen XML exports (``<project ...
xmlns="http://www.plcopen.org/xml/tc6_0201">``).

Extracts the pieces the soft PLC runtime needs: global variables (with
initial values), per-POU local variables/constants/timers, each POU's body
program(s) parsed into ST statement lists, and the Initial/Scan program
execution order.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .st_lang import TimerState, parse

_TIMER_TYPES = {"TIMER", "TON", "TOF", "TP"}

# IEC 61131 typed numeric literals as used for initial values, e.g. K20
# (decimal) or H1F (hexadecimal).
_K_CONST_RE = re.compile(r"^K(-?\d+)$", re.IGNORECASE)
_H_CONST_RE = re.compile(r"^H([0-9A-F]+)$", re.IGNORECASE)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _findall(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in elem if _local(c.tag) == name]


def _adddata_child(elem: ET.Element, suffix: str) -> ET.Element | None:
    """Find ``<addData><data name="...suffix"><CHILD/></data></addData>``
    and return CHILD."""
    add_data = _find(elem, "addData")
    if add_data is None:
        return None
    for data in _findall(add_data, "data"):
        if data.get("name", "").endswith(suffix):
            children = list(data)
            if children:
                return children[0]
    return None


def _type_name(var_elem: ET.Element) -> str | None:
    type_elem = _find(var_elem, "type")
    if type_elem is None:
        return None
    children = list(type_elem)
    if not children:
        return None
    child = children[0]
    if _local(child.tag) == "derived":
        return child.get("name")
    return _local(child.tag)


def _initial_value(var_elem: ET.Element, type_name: str | None):
    if type_name in _TIMER_TYPES:
        return TimerState()
    initial = _find(var_elem, "initialValue")
    if initial is None:
        return False if type_name == "BOOL" else 0
    simple = _find(initial, "simpleValue")
    raw = simple.get("value") if simple is not None else None
    if raw is None:
        return False if type_name == "BOOL" else 0
    if type_name == "BOOL":
        return raw.strip().upper() == "TRUE"
    raw = raw.strip()
    m = _K_CONST_RE.match(raw)
    if m:
        return int(m.group(1))
    m = _H_CONST_RE.match(raw)
    if m:
        return int(m.group(1), 16)
    return int(raw)


def _st_source(body_elem: ET.Element) -> str:
    st = _find(body_elem, "ST")
    if st is None:
        return ""
    html = _find(st, "html")
    return (html.text or "") if html is not None else ""


@dataclass
class Pou:
    name: str
    local_vars: dict = field(default_factory=dict)   # name_lower -> initial value / TimerState
    bodies: list = field(default_factory=list)        # list[(worksheet_name, stmts)]


@dataclass
class Project:
    global_vars: dict
    pous: dict
    initial_pous: list
    scan_pous: list


def _load_pou(pou_elem: ET.Element) -> Pou:
    pou = Pou(name=pou_elem.get("name"))

    interface = _find(pou_elem, "interface")
    if interface is not None:
        for local_vars in _findall(interface, "localVars"):
            for var in _findall(local_vars, "variable"):
                name = var.get("name")
                type_name = _type_name(var)
                pou.local_vars[name.lower()] = _initial_value(var, type_name)

    bodies = []
    for index, body_elem in enumerate(_findall(pou_elem, "body")):
        body_props = _adddata_child(body_elem, "bodyProperties")
        order = int(body_props.get("executeOrder", index)) if body_props is not None else index
        worksheet = body_elem.get("WorksheetName", f"body{index}")
        stmts = parse(_st_source(body_elem))
        bodies.append((order, worksheet, stmts))
    bodies.sort(key=lambda b: b[0])
    pou.bodies = [(name, stmts) for _order, name, stmts in bodies]

    return pou


def _load_global_vars(configuration: ET.Element) -> dict:
    global_vars = {}
    for group in _findall(configuration, "globalVars"):
        for var in _findall(group, "variable"):
            name = var.get("name")
            type_name = _type_name(var)
            global_vars[name.lower()] = _initial_value(var, type_name)
    return global_vars


def _load_resource_pous(resource: ET.Element) -> tuple[str, list[str]]:
    exec_type_elem = _adddata_child(resource, "resourceExecutionType")
    exec_type = exec_type_elem.get("type") if exec_type_elem is not None else "scan"

    ordered = []
    for instance in _findall(resource, "pouInstance"):
        pos_elem = _adddata_child(instance, "pouInstancePosition")
        order = int(pos_elem.get("order")) if pos_elem is not None else len(ordered)
        ordered.append((order, instance.get("name")))
    ordered.sort(key=lambda p: p[0])
    return exec_type, [name for _order, name in ordered]


def load_project(xml_path: str) -> Project:
    root = ET.parse(xml_path).getroot()

    pous = {}
    types_elem = _find(root, "types")
    if types_elem is not None:
        pous_elem = _find(types_elem, "pous")
        if pous_elem is not None:
            for pou_elem in _findall(pous_elem, "pou"):
                pou = _load_pou(pou_elem)
                pous[pou.name] = pou

    global_vars = {}
    initial_pous: list[str] = []
    scan_pous: list[str] = []
    instances_elem = _find(root, "instances")
    if instances_elem is not None:
        configs_elem = _find(instances_elem, "configurations")
        if configs_elem is not None:
            for configuration in _findall(configs_elem, "configuration"):
                global_vars.update(_load_global_vars(configuration))
                for resource in _findall(configuration, "resource"):
                    exec_type, names = _load_resource_pous(resource)
                    if exec_type == "initial":
                        initial_pous.extend(names)
                    else:
                        scan_pous.extend(names)

    return Project(global_vars=global_vars, pous=pous,
                    initial_pous=initial_pous, scan_pous=scan_pous)
