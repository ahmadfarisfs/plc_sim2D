"""A small Structured Text (IEC 61131-3 / GX Works3) lexer, parser and
interpreter, scoped to the subset of ST used by the exported PLC program:

* comments: ``(* ... *)`` and ``// ...``
* statements: ``lhs := expr;``, ``IF/ELSIF/ELSE/END_IF``,
  ``CASE expr OF label[, label]*: stmts ... [ELSE: stmts] END_CASE``,
  ``RETURN;``, bare function calls ``NAME(args);``
* expressions: ``OR``, ``AND``, ``NOT`` (unary), ``= <> < <= > >=``,
  ``+ - * /``, parentheses, dotted member access (``TP_Eject.S``),
  literals ``TRUE``/``FALSE``, integers, ``K<int>`` constants
* identifiers/devices are resolved case-insensitively; raw ``M<n>``/``X<n>``/
  ``Y<n>`` tokens are treated as devices, separate from named variables

This is not a general IEC ST compiler -- constructs outside the above
(arrays, structs, FOR/WHILE loops, REPEAT, function blocks beyond simple
timers, etc.) will raise ``SyntaxError`` from the parser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

KEYWORDS = {
    "IF", "THEN", "ELSIF", "ELSE", "END_IF",
    "CASE", "OF", "END_CASE",
    "RETURN", "AND", "OR", "NOT", "TRUE", "FALSE",
}

DEVICE_RE = re.compile(r"^[MXY]\d+$", re.IGNORECASE)
K_CONST_RE = re.compile(r"^K(\d+)$", re.IGNORECASE)

_TOKEN_SPEC = [
    ("WS", r"[ \t\r\n]+"),
    ("LCOMMENT", r"//[^\n]*"),
    ("BCOMMENT", r"\(\*.*?\*\)"),
    ("ASSIGN", r":="),
    ("LE", r"<="),
    ("GE", r">="),
    ("NE", r"<>"),
    ("EQ", r"="),
    ("LT", r"<"),
    ("GT", r">"),
    ("COLON", r":"),
    ("SEMI", r";"),
    ("COMMA", r","),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("DOT", r"\."),
    ("PLUS", r"\+"),
    ("MINUS", r"-"),
    ("MUL", r"\*"),
    ("DIV", r"/"),
    ("NUMBER", r"\d+"),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
]
_TOKEN_RE = re.compile(
    "|".join(f"(?P<{name}>{pat})" for name, pat in _TOKEN_SPEC), re.DOTALL)


@dataclass
class Token:
    type: str
    value: str


def tokenize(src: str) -> list[Token]:
    tokens = []
    pos = 0
    n = len(src)
    while pos < n:
        m = _TOKEN_RE.match(src, pos)
        if not m:
            raise SyntaxError(f"unexpected character {src[pos]!r} at offset {pos}")
        kind = m.lastgroup
        text = m.group()
        pos = m.end()
        if kind in ("WS", "LCOMMENT", "BCOMMENT"):
            continue
        tokens.append(Token(kind, text))
    tokens.append(Token("EOF", ""))
    return tokens


# ---------------------------------------------------------------- AST nodes

@dataclass
class Lit:
    value: object


@dataclass
class Var:
    name: str


@dataclass
class Member:
    name: str
    member: str


@dataclass
class UnaryOp:
    op: str
    expr: object


@dataclass
class BinOp:
    op: str
    left: object
    right: object


@dataclass
class Assign:
    target: tuple
    expr: object


@dataclass
class IfStmt:
    cond: object
    then_body: list
    elifs: list = field(default_factory=list)   # list[(cond, body)]
    else_body: list | None = None


@dataclass
class CaseStmt:
    selector: object
    branches: list      # list[(labels: list[expr], body: list)]
    else_body: list | None = None


@dataclass
class ReturnStmt:
    pass


@dataclass
class CallStmt:
    name: str
    args: list


# -------------------------------------------------------------------- parser

_BIN_OPS = {"EQ": "=", "NE": "<>", "LT": "<", "GT": ">", "LE": "<=", "GE": ">=",
            "PLUS": "+", "MINUS": "-", "MUL": "*", "DIV": "/"}


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def cur(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def at(self, *types: str) -> bool:
        return self.cur().type in types

    def at_kw(self, kw: str) -> bool:
        tok = self.cur()
        return tok.type == "IDENT" and tok.value.upper() == kw

    def peek_kw(self, kw: str, offset: int = 1) -> bool:
        tok = self.tokens[self.pos + offset]
        return tok.type == "IDENT" and tok.value.upper() == kw

    def expect(self, type_: str) -> Token:
        if self.cur().type != type_:
            raise SyntaxError(f"expected {type_}, got {self.cur().type} "
                              f"{self.cur().value!r}")
        return self.advance()

    def expect_kw(self, kw: str) -> Token:
        if not self.at_kw(kw):
            raise SyntaxError(f"expected {kw!r}, got {self.cur().value!r}")
        return self.advance()

    # ---- program ---------------------------------------------------------

    def parse_program(self) -> list:
        stmts = self.parse_stmt_list(("EOF",))
        self.expect("EOF")
        return stmts

    def parse_stmt_list(self, until_kws: tuple[str, ...]) -> list:
        stmts = []
        while not (self.cur().type == "EOF" or
                   (self.cur().type == "IDENT" and self.cur().value.upper() in until_kws)):
            stmts.append(self.parse_stmt())
        return stmts

    def parse_case_body(self) -> list:
        """Statement list inside a CASE branch: stops at END_CASE/ELSE or
        the next ``label:`` (an IDENT/NUMBER immediately followed by COLON,
        not ASSIGN)."""
        stmts = []
        while True:
            tok = self.cur()
            if tok.type == "EOF":
                break
            if tok.type == "IDENT" and tok.value.upper() in ("END_CASE", "ELSE"):
                break
            if tok.type in ("IDENT", "NUMBER") and self.tokens[self.pos + 1].type == "COLON":
                break
            stmts.append(self.parse_stmt())
        return stmts

    # ---- statements --------------------------------------------------------

    def parse_stmt(self):
        if self.at_kw("IF"):
            return self.parse_if()
        if self.at_kw("CASE"):
            return self.parse_case()
        if self.at_kw("RETURN"):
            self.advance()
            self.expect("SEMI")
            return ReturnStmt()
        if self.cur().type == "IDENT" and self.tokens[self.pos + 1].type == "LPAREN":
            return self.parse_call_stmt()
        return self.parse_assign()

    def parse_if(self) -> IfStmt:
        self.expect_kw("IF")
        cond = self.parse_expr()
        self.expect_kw("THEN")
        then_body = self.parse_stmt_list(("ELSIF", "ELSE", "END_IF"))
        elifs = []
        while self.at_kw("ELSIF"):
            self.advance()
            econd = self.parse_expr()
            self.expect_kw("THEN")
            ebody = self.parse_stmt_list(("ELSIF", "ELSE", "END_IF"))
            elifs.append((econd, ebody))
        else_body = None
        if self.at_kw("ELSE"):
            self.advance()
            else_body = self.parse_stmt_list(("END_IF",))
        self.expect_kw("END_IF")
        self.expect("SEMI")
        return IfStmt(cond, then_body, elifs, else_body)

    def parse_case_label(self):
        tok = self.cur()
        if tok.type == "NUMBER":
            self.advance()
            return Lit(int(tok.value))
        if tok.type == "IDENT":
            self.advance()
            return Var(tok.value)
        raise SyntaxError(f"invalid CASE label {tok.value!r}")

    def parse_case(self) -> CaseStmt:
        self.expect_kw("CASE")
        selector = self.parse_expr()
        self.expect_kw("OF")
        branches = []
        else_body = None
        while not self.at_kw("END_CASE"):
            if self.at_kw("ELSE"):
                self.advance()
                self.expect("COLON")
                else_body = self.parse_case_body()
                break
            labels = [self.parse_case_label()]
            while self.at("COMMA"):
                self.advance()
                labels.append(self.parse_case_label())
            self.expect("COLON")
            body = self.parse_case_body()
            branches.append((labels, body))
        self.expect_kw("END_CASE")
        self.expect("SEMI")
        return CaseStmt(selector, branches, else_body)

    def parse_call_stmt(self) -> CallStmt:
        name = self.expect("IDENT").value
        self.expect("LPAREN")
        args = []
        if not self.at("RPAREN"):
            args.append(self.parse_expr())
            while self.at("COMMA"):
                self.advance()
                args.append(self.parse_expr())
        self.expect("RPAREN")
        self.expect("SEMI")
        return CallStmt(name, args)

    def parse_assign(self) -> Assign:
        name = self.expect("IDENT").value
        if self.at("DOT"):
            self.advance()
            member = self.expect("IDENT").value
            target = ("member", name, member)
        else:
            target = ("var", name)
        self.expect("ASSIGN")
        expr = self.parse_expr()
        self.expect("SEMI")
        return Assign(target, expr)

    # ---- expressions (lowest to highest precedence) -----------------------

    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.at_kw("OR"):
            self.advance()
            left = BinOp("OR", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_equality()
        while self.at_kw("AND"):
            self.advance()
            left = BinOp("AND", left, self.parse_equality())
        return left

    def parse_equality(self):
        left = self.parse_relational()
        while self.at("EQ", "NE"):
            op = self.advance().type
            left = BinOp(_BIN_OPS[op], left, self.parse_relational())
        return left

    def parse_relational(self):
        left = self.parse_additive()
        while self.at("LT", "GT", "LE", "GE"):
            op = self.advance().type
            left = BinOp(_BIN_OPS[op], left, self.parse_additive())
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.at("PLUS", "MINUS"):
            op = self.advance().type
            left = BinOp(_BIN_OPS[op], left, self.parse_multiplicative())
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.at("MUL", "DIV"):
            op = self.advance().type
            left = BinOp(_BIN_OPS[op], left, self.parse_unary())
        return left

    def parse_unary(self):
        if self.at_kw("NOT"):
            self.advance()
            return UnaryOp("NOT", self.parse_unary())
        if self.at("MINUS"):
            self.advance()
            return UnaryOp("NEG", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        tok = self.cur()
        if tok.type == "LPAREN":
            self.advance()
            expr = self.parse_expr()
            self.expect("RPAREN")
            return expr
        if tok.type == "NUMBER":
            self.advance()
            return Lit(int(tok.value))
        if tok.type == "IDENT":
            self.advance()
            upper = tok.value.upper()
            if upper == "TRUE":
                return Lit(True)
            if upper == "FALSE":
                return Lit(False)
            m = K_CONST_RE.match(tok.value)
            if m:
                return Lit(int(m.group(1)))
            if self.at("DOT"):
                self.advance()
                member = self.expect("IDENT").value
                return Member(tok.value, member)
            return Var(tok.value)
        raise SyntaxError(f"unexpected token {tok.type} {tok.value!r}")


def parse(src: str) -> list:
    return Parser(tokenize(src)).parse_program()


# --------------------------------------------------------------- interpreter

@dataclass
class TimerState:
    et: float = 0.0   # elapsed time, seconds
    s: bool = False    # done bit


class ReturnSignal(Exception):
    """Raised by RETURN; caught at the enclosing body's top level."""


def truthy(v) -> bool:
    return bool(v)


class Scope:
    """Variable resolution for one POU instance during one statement run.

    Lookup order: raw M/X/Y device -> POU-local vars/constants/timers ->
    shared global vars. Names are matched case-insensitively.
    """

    def __init__(self, globals_: dict, locals_: dict, devices: dict, timers: dict):
        self.globals = globals_
        self.locals = locals_
        self.devices = devices
        self.timers = timers

    def get(self, name: str):
        if DEVICE_RE.match(name):
            return self.devices.get(name.upper(), False)
        key = name.lower()
        if key in self.locals:
            return self.locals[key]
        if key in self.globals:
            return self.globals[key]
        return False

    def set(self, name: str, value) -> None:
        if DEVICE_RE.match(name):
            self.devices[name.upper()] = value
            return
        key = name.lower()
        if key in self.locals:
            self.locals[key] = value
        elif key in self.globals:
            self.globals[key] = value
        else:
            self.locals[key] = value

    def get_timer(self, name: str) -> TimerState:
        return self.timers[name.lower()]

    def get_member(self, name: str, member: str):
        timer = self.get_timer(name)
        return timer.et if member.upper() == "ET" else timer.s


class Interpreter:
    """Executes a parsed ST statement list against a Scope.

    ``call_dispatch`` maps uppercase function names (``OUT_T``, ``PLS``) to
    callables ``(interp, args, scope, dt) -> None``; unrecognized calls are
    ignored.
    """

    def __init__(self, call_dispatch: dict):
        self.call_dispatch = call_dispatch
        self.pls_prev: dict = {}

    def exec_body(self, stmts: list, scope: Scope, dt: float) -> None:
        try:
            self.exec_block(stmts, scope, dt)
        except ReturnSignal:
            pass

    def exec_block(self, stmts: list, scope: Scope, dt: float) -> None:
        for stmt in stmts:
            self.exec_stmt(stmt, scope, dt)

    def exec_stmt(self, stmt, scope: Scope, dt: float) -> None:
        if isinstance(stmt, Assign):
            value = self.eval(stmt.expr, scope)
            kind = stmt.target[0]
            if kind == "var":
                scope.set(stmt.target[1], value)
            else:  # member, e.g. assigning a timer field directly
                timer = scope.get_timer(stmt.target[1])
                setattr(timer, stmt.target[2].lower(), value)
        elif isinstance(stmt, IfStmt):
            if truthy(self.eval(stmt.cond, scope)):
                self.exec_block(stmt.then_body, scope, dt)
                return
            for econd, ebody in stmt.elifs:
                if truthy(self.eval(econd, scope)):
                    self.exec_block(ebody, scope, dt)
                    return
            if stmt.else_body is not None:
                self.exec_block(stmt.else_body, scope, dt)
        elif isinstance(stmt, CaseStmt):
            selector = self.eval(stmt.selector, scope)
            for labels, body in stmt.branches:
                if any(self.eval(lab, scope) == selector for lab in labels):
                    self.exec_block(body, scope, dt)
                    return
            if stmt.else_body is not None:
                self.exec_block(stmt.else_body, scope, dt)
        elif isinstance(stmt, ReturnStmt):
            raise ReturnSignal()
        elif isinstance(stmt, CallStmt):
            fn = self.call_dispatch.get(stmt.name.upper())
            if fn is not None:
                fn(self, stmt.args, scope, dt)
        else:
            raise TypeError(f"unknown statement node {stmt!r}")

    def eval(self, node, scope: Scope):
        if isinstance(node, Lit):
            return node.value
        if isinstance(node, Var):
            return scope.get(node.name)
        if isinstance(node, Member):
            return scope.get_member(node.name, node.member)
        if isinstance(node, UnaryOp):
            value = self.eval(node.expr, scope)
            return (not truthy(value)) if node.op == "NOT" else -value
        if isinstance(node, BinOp):
            left = self.eval(node.left, scope)
            if node.op == "AND":
                return truthy(left) and truthy(self.eval(node.right, scope))
            if node.op == "OR":
                return truthy(left) or truthy(self.eval(node.right, scope))
            right = self.eval(node.right, scope)
            if node.op == "=":
                return left == right
            if node.op == "<>":
                return left != right
            if node.op == "<":
                return left < right
            if node.op == ">":
                return left > right
            if node.op == "<=":
                return left <= right
            if node.op == ">=":
                return left >= right
            if node.op == "+":
                return left + right
            if node.op == "-":
                return left - right
            if node.op == "*":
                return left * right
            if node.op == "/":
                return left / right
        raise TypeError(f"unknown expression node {node!r}")
