"""Third-party rule packs.

A package can ship extra Python passes (for its own operator library, internal frameworks, or a
provider's own renames) by exposing an entry point in the ``dagshift.rules`` group:

    # pyproject.toml of your rule pack
    [project.entry-points."dagshift.rules"]
    acme = "acme_dagshift:register"

    # acme_dagshift/__init__.py
    from dagshift.python.base import Pass
    from dagshift.findings import Severity

    class AcmeOperatorRename(Pass):
        def leave_Call(self, original_node, updated_node):
            ...
            return updated_node

    def register(registry):
        registry.add_python_pass(AcmeOperatorRename)
        registry.add_moves({"acme.ops.LegacyOperator": "acme.ops.v3.Operator"})

Import moves alone need no code: put them in [tool.dagshift] extra_moves.
"""
from __future__ import annotations

from importlib.metadata import entry_points

from .python import migrator

_LOADED = False
_EXTRA_MOVES: dict[str, str] = {}


class Registry:
    def add_python_pass(self, cls: type) -> None:
        if cls not in migrator.EXTRA_PASSES:
            migrator.EXTRA_PASSES.append(cls)

    def add_moves(self, moves: dict[str, str]) -> None:
        _EXTRA_MOVES.update(moves)


def load_plugins() -> dict[str, str]:
    """Load every installed rule pack once. Returns extra import moves they contributed."""
    global _LOADED
    if not _LOADED:
        _LOADED = True
        reg = Registry()
        for ep in entry_points(group="dagshift.rules"):
            ep.load()(reg)
    return dict(_EXTRA_MOVES)
