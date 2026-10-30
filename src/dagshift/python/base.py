"""Base class for dagshift Python passes."""
from __future__ import annotations

import libcst as cst
from libcst.codemod import CodemodContext, VisitorBasedCodemodCommand
from libcst.metadata import PositionProvider

from ..findings import Severity
from .state import FileState


class Pass(VisitorBasedCodemodCommand):
    """A libcst codemod with access to the file's FileState.

    Subclasses add findings with self.report(rule_id, severity, node, message, recipe).
    Imports are added/removed with self.add_import / self.remove_import_if_unused, which
    libcst applies (merged and de-duplicated) after the pass finishes.
    """

    DESCRIPTION = ""
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, context: CodemodContext, state: FileState) -> None:
        super().__init__(context)
        self.state = state
        self.kb = state.kb
        self.options = state.options

    def line_of(self, node: cst.CSTNode) -> int:
        try:
            return self.get_metadata(PositionProvider, node).start.line
        except Exception:
            return 1

    def report(self, rule_id: str, severity: Severity, node: cst.CSTNode | int, message: str,
               recipe: str | None = None) -> None:
        line = node if isinstance(node, int) else self.line_of(node)
        self.state.add(rule_id, severity, line, message, recipe)

    def enabled(self, rule_id: str) -> bool:
        return self.state.enabled(rule_id)

    def add_import(self, module: str, obj: str | None = None, asname: str | None = None) -> None:
        from libcst.codemod.visitors import AddImportsVisitor

        AddImportsVisitor.add_needed_import(self.context, module, obj, asname)

    def remove_import_if_unused(self, module: str, obj: str | None = None, asname: str | None = None) -> None:
        from libcst.codemod.visitors import RemoveImportsVisitor

        RemoveImportsVisitor.remove_unused_import(self.context, module, obj, asname)
