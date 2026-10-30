"""Run every Python pass over one source file."""
from __future__ import annotations

import re

import libcst as cst
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor, RemoveImportsVisitor
from libcst.metadata import MetadataWrapper

from ..findings import FileResult, Severity
from ..jinja_mask import mask
from ..jinja_text import ContextPolicy
from ..kb import KnowledgeBase
from ..options import Options
from .passes.calls import CallsPass
from .passes.context import ContextPass
from .passes.imports import ImportsPass
from .passes.metadata_db import MetadataDbPass
from .passes.operators import ReplacedOperatorsPass
from .passes.safety import SafetyPass
from .passes.strings import StringsPass
from .state import FileState, LineMap

# Order matters: analysis that feeds later passes first, import rewriting last.
PASSES: list[type] = [StringsPass, ContextPass, MetadataDbPass, SafetyPass, CallsPass, ReplacedOperatorsPass,
                      ImportsPass]
EXTRA_PASSES: list[type] = []  # filled by plugins (see dagshift.plugins)

_IMPORTS_AIRFLOW = re.compile(r"^\s*(from\s+airflow[\s.]|import\s+airflow\b)", re.M)
_GATES: dict[type, re.Pattern] = {}


def _gate(pass_cls: type, tokens) -> re.Pattern:
    """Whole-word regex of the tokens a pass needs (cached per pass class)."""
    rx = _GATES.get(pass_cls)
    if rx is None:
        rx = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, tokens), key=len, reverse=True)) + r")\b")
        _GATES[pass_cls] = rx
    return rx


def migrate_python_source(
    source: str,
    path: str = "<string>",
    kb: KnowledgeBase | None = None,
    options: Options | None = None,
    depth: int = 0,
) -> FileResult:
    options = options or Options()
    kb = kb or KnowledgeBase(options.target, options.extra_moves, options.source)
    masked = mask(source)
    try:
        module = cst.parse_module(masked.code)
    except cst.ParserSyntaxError as exc:
        res = FileResult(path=path, original=source, migrated=source, templated=masked.templated,
                         error=f"could not parse as Python: {exc.message} (line {exc.raw_line})")
        return res

    state = FileState(path=path, kb=kb, options=options, masked=masked, original=source)
    state.imports_airflow = bool(_IMPORTS_AIRFLOW.search(masked.code))
    state.policy = ContextPolicy.from_kb(kb, options)
    current = masked.code
    passes = PASSES[:-1] + EXTRA_PASSES + PASSES[-1:]
    # One metadata wrapper per version of the code: passes that change nothing (most analysis
    # passes) reuse the scope / qualified-name / position metadata the previous pass computed.
    wrapper = MetadataWrapper(module)
    line_map_for = None
    for pass_cls in passes:
        gate = getattr(pass_cls, "REQUIRES_ANY", None)
        if gate is not None and not _gate(pass_cls, gate).search(current):
            continue  # nothing this pass looks for is in the file
        if line_map_for != current:
            state.line_map = LineMap(source, current)
            line_map_for = current
        ctx = CodemodContext(filename=path, full_module_name=None, wrapper=wrapper)
        instance = pass_cls(ctx, state, depth=depth) if pass_cls is StringsPass else pass_cls(ctx, state)
        with instance.resolve(wrapper):
            new_module = wrapper.module.visit(instance)
        for key, follow_up in ((AddImportsVisitor.CONTEXT_KEY, AddImportsVisitor),
                               (RemoveImportsVisitor.CONTEXT_KEY, RemoveImportsVisitor)):
            if key in instance.context.scratch:
                new_module = follow_up(instance.context).transform_module(new_module)
        new_code = new_module.code
        if new_code != current:
            current = new_code
            wrapper = MetadataWrapper(new_module)

    migrated = masked.unmask(current)
    if migrated != source:
        try:
            cst.parse_module(mask(migrated).code)
        except cst.ParserSyntaxError as exc:  # never hand back broken code
            return FileResult(path=path, original=source, migrated=source, findings=state.findings,
                              templated=masked.templated,
                              error=f"internal: migrated code did not parse ({exc.message}); file left unchanged")
    if masked.templated and depth == 0:
        state.line_map = None
        state.add("DS505", Severity.INFO, 1,
                  f"Jinja template: {len(masked.placeholders)} Jinja construct(s) in code positions were kept "
                  "as-is. Values your generator injects there (schedules, operator classes, kwargs) are only "
                  "visible after rendering.",
                  "Render the templates and run `dagshift check` on the output, or use "
                  "`dagshift validate --render-cmd '<your build command>'`.")
    state.findings.sort(key=lambda f: (f.line, f.rule_id))
    return FileResult(path=path, original=source, migrated=migrated, findings=state.findings,
                      templated=masked.templated)
