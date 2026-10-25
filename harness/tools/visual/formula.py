"""The formulas a what-if block computes with, checked before the GUI ever runs one.

The GUI evaluates them (`surfaces/gui/src/components/visual/formula.ts`) as the person moves a
slider; this module only checks that one is in the grammar both sides share and names nothing
undeclared, so a model that wrote a bad formula hears it in the tool result instead of the person
seeing a blank number. The grammar is small on purpose: arithmetic, a few functions, no
comparison, no assignment, no attribute or index, nothing that reaches outside the numbers.

    expr   := term (("+" | "-") term)*
    term   := unary (("*" | "/" | "%") unary)*
    unary  := "-" unary | power
    power  := atom (("**" | "^") unary)?
    atom   := number | name | name "(" [expr ("," expr)*] ")" | "(" expr ")"
"""

from __future__ import annotations

import re

#: Function name -> how many arguments it takes, as (fewest, most).
FUNCTIONS: dict[str, tuple[int, int]] = {
    "min": (1, 16),
    "max": (1, 16),
    "abs": (1, 1),
    "round": (1, 2),
    "floor": (1, 1),
    "ceil": (1, 1),
    "sqrt": (1, 1),
    "pow": (2, 2),
    "log": (1, 1),
    "exp": (1, 1),
    # Payment per period of a loan: rate per period, number of periods, principal.
    "pmt": (3, 3),
}

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)|([A-Za-z_]\w*)|(\*\*|[-+*/%^(),]))")

MAX_LENGTH = 400


class FormulaError(ValueError):
    pass


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise FormulaError(f"cannot read {text[pos:pos + 12]!r}")
        number, name, op = match.groups()
        out.append(("num", number) if number else ("name", name) if name else ("op", op))
        pos = match.end()
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], names: set[str]) -> None:
        self.tokens = tokens
        self.i = 0
        self.names = names

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, value: str) -> bool:
        token = self.peek()
        if token and token[0] == "op" and token[1] == value:
            self.i += 1
            return True
        return False

    def expr(self) -> None:
        self.term()
        while self.take("+") or self.take("-"):
            self.term()

    def term(self) -> None:
        self.unary()
        while self.take("*") or self.take("/") or self.take("%"):
            self.unary()

    def unary(self) -> None:
        if self.take("-") or self.take("+"):
            self.unary()
            return
        self.atom()
        if self.take("**") or self.take("^"):
            self.unary()

    def atom(self) -> None:
        token = self.peek()
        if token is None:
            raise FormulaError("ends too early")
        kind, value = token
        self.i += 1
        if kind == "num":
            return
        if kind == "name":
            if self.take("("):
                if value not in FUNCTIONS:
                    raise FormulaError(f"unknown function {value}() (known: {', '.join(sorted(FUNCTIONS))})")
                count = 0
                if not self.take(")"):
                    while True:
                        self.expr()
                        count += 1
                        if self.take(")"):
                            break
                        if not self.take(","):
                            raise FormulaError(f"expected ',' or ')' in {value}()")
                low, high = FUNCTIONS[value]
                if not low <= count <= high:
                    raise FormulaError(f"{value}() takes {low if low == high else f'{low} to {high}'} arguments, got {count}")
                return
            if value not in self.names:
                raise FormulaError(f"unknown name {value!r}")
            return
        if value == "(":
            self.expr()
            if not self.take(")"):
                raise FormulaError("a '(' is not closed")
            return
        raise FormulaError(f"unexpected {value!r}")


def check_formula(text: object, names: set[str]) -> None:
    """Raise FormulaError when `text` is not a formula over `names` in the shared grammar."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return
    if not isinstance(text, str) or not text.strip():
        raise FormulaError("a formula must be a non-empty string")
    if len(text) > MAX_LENGTH:
        raise FormulaError(f"a formula is at most {MAX_LENGTH} characters")
    parser = _Parser(_tokens(text), names)
    parser.expr()
    if parser.peek() is not None:
        raise FormulaError(f"unexpected {parser.peek()[1]!r}")


__all__ = ["FUNCTIONS", "FormulaError", "check_formula"]
