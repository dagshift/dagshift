"""Make Jinja-templated DAG files parseable as Python, then put the Jinja back.

DAG factories often keep a Python file with Jinja in *code* positions, for example
``schedule={{ cfg.schedule }},`` or ``{% for t in tables %}`` blocks, and render it at
build time. A Python parser cannot read those files, so linters either skip them or
treat ``{{ a | b }}`` as a nested set literal.

Strategy:
  1. Lex the file with ``tokenize`` to find string literal and comment spans.
  2. Every Jinja construct (``{{ }}``, ``{% %}``, ``{# #}``) that starts *outside* a string
     is replaced with a unique identifier. A bare identifier is valid in any expression
     position and as a statement on its own line, so the file now parses.
  3. Jinja inside string literals is left alone. Rules that need to see it (removed context
     variables, xcom_pull calls) read the string text directly.
  4. After the codemods run, identifiers are swapped back for the original Jinja text.
"""
from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass, field

_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)
_PLACEHOLDER = "__DAGSHIFT_J{}__"
_LINE_PLACEHOLDER = "#__DAGSHIFT_L{}__"
_PLACEHOLDER_RE = re.compile(r"#__DAGSHIFT_L(\d+)__|__DAGSHIFT_J(\d+)__")
# A Jinja statement or comment alone on its line: masked as a Python comment so it does not
# disturb indentation ({% for %} / {% if %} bodies stay valid Python blocks).
_STMT_LINE = re.compile(r"^([ \t]*)(\{%.*?%\}|\{#.*?#\})[ \t]*$", re.M)

_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
_TSTRING_START = getattr(tokenize, "TSTRING_START", None)
_TSTRING_END = getattr(tokenize, "TSTRING_END", None)


@dataclass
class MaskedSource:
    code: str
    placeholders: dict[str, str] = field(default_factory=dict)

    @property
    def templated(self) -> bool:
        return bool(self.placeholders)

    def unmask(self, text: str) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: self.placeholders.get(m.group(0), m.group(0)), text)

    def is_placeholder(self, name: str) -> bool:
        return name in self.placeholders


def _line_offsets(src: str) -> list[int]:
    offsets = [0]
    for line in src.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _protected_spans(src: str) -> list[tuple[int, int]] | None:
    """Spans of string literals and comments. None if the file cannot be lexed."""
    offsets = _line_offsets(src)

    def off(pos: tuple[int, int]) -> int:
        row, col = pos
        return offsets[row - 1] + col

    spans: list[tuple[int, int]] = []
    starts: list[int] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                spans.append((off(tok.start), off(tok.end)))
            elif tok.type in (_FSTRING_START, _TSTRING_START) and tok.type is not None:
                starts.append(off(tok.start))
            elif tok.type in (_FSTRING_END, _TSTRING_END) and tok.type is not None and starts:
                spans.append((starts.pop(), off(tok.end)))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return spans


def mask(src: str) -> MaskedSource:
    if "{{" not in src and "{%" not in src and "{#" not in src:
        return MaskedSource(code=src)

    placeholders: dict[str, str] = {}

    # 1. statement/comment lines -> Python comments
    def _line(m: re.Match) -> str:
        key = _LINE_PLACEHOLDER.format(len(placeholders))
        placeholders[key] = m.group(2)
        return m.group(1) + key

    src1 = _STMT_LINE.sub(_line, src)

    # 2. everything else outside string literals -> identifiers
    spans = _protected_spans(src1)

    def inside_protected(pos: int) -> bool:
        if spans is None:
            return False
        return any(a <= pos < b for a, b in spans)

    out: list[str] = []
    last = 0
    for m in _JINJA.finditer(src1):
        if inside_protected(m.start()):
            continue
        key = _PLACEHOLDER.format(len(placeholders))
        placeholders[key] = m.group(0)
        out.append(src1[last : m.start()])
        out.append(key)
        last = m.end()
    out.append(src1[last:])
    return MaskedSource(code="".join(out), placeholders=placeholders)
