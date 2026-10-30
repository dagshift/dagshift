"""Versioned Airflow knowledge base.

Built by tools/build_kb.py from real installs of every supported Airflow minor (2.7 to 3.3): every
import path status (ok / deprecated / removed), its replacement, context keys and signatures were
observed by importing them, not typed in by hand.

A KnowledgeBase answers questions for one upgrade: from `source` (what the project runs today, may be
unknown) to `target` (what it is moving to).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

DEFAULT_TARGET = "3.3"


@dataclass(frozen=True)
class Move:
    old: str
    new: str | None
    status: str  # ok | deprecated | removed | custom
    internal: bool = False
    note: str | None = None

    @property
    def required(self) -> bool:
        """True when the old path breaks or warns on the target."""
        return self.status in ("removed", "deprecated", "custom")

    @property
    def new_module(self) -> str | None:
        return self.new.rpartition(".")[0] if self.new else None

    @property
    def new_name(self) -> str | None:
        return self.new.rpartition(".")[2] if self.new else None


@lru_cache(maxsize=1)
def _raw() -> dict:
    text = resources.files("dagshift").joinpath("_data/kb.json").read_text(encoding="utf-8")
    return json.loads(text)


def _vt(v: str) -> tuple[int, int]:
    from .versions import vtuple

    return vtuple(v)


def available_targets() -> list[str]:
    return sorted(_raw()["targets"], key=_vt)


def normalize_target(target: str | None) -> str:
    targets = available_targets()
    if not target:
        return DEFAULT_TARGET if DEFAULT_TARGET in targets else targets[-1]
    t = str(target).strip()
    if t in ("3", "latest"):
        return targets[-1]
    wanted = _vt(t)
    exact = f"{wanted[0]}.{wanted[1]}"
    if exact in targets:
        return exact
    if wanted > _vt(targets[-1]) and wanted[0] == _vt(targets[-1])[0]:
        return targets[-1]  # newer minor than anything probed: use the newest knowledge available
    raise ValueError(f"unknown target Airflow {target!r}; supported targets: {', '.join(targets)}")


class KnowledgeBase:
    def __init__(self, target: str | None = None, extra_moves: dict[str, str] | None = None,
                 source: str | None = None) -> None:
        self.raw = _raw()
        self.target = normalize_target(target)
        self.built_from = self.raw["built_from"][self.target]
        self.extra_moves = dict(extra_moves or {})
        self.source = None
        if source and str(source).lower() != "auto":
            st = _vt(source)
            if st == (0, 0):
                raise ValueError(f"unrecognised source version {source!r}")
            self.source = f"{st[0]}.{st[1]}"
        self.target_t = _vt(self.target)
        self.source_t = _vt(self.source) if self.source else None
        if self.source_t and self.source_t > self.target_t:
            raise ValueError(f"target Airflow {self.target} is older than the current version {self.source}; "
                             "dagshift only upgrades")

    # ---- version questions ---------------------------------------------------------
    def target_at_least(self, version: str) -> bool:
        return self.target_t >= _vt(version)

    def crosses(self, version: str) -> bool:
        """True when this upgrade goes from below `version` to `version` or later.

        An unknown source counts as below, so behaviour notes are shown rather than hidden.
        """
        boundary = _vt(version)
        return (self.source_t or (0, 0)) < boundary <= self.target_t

    @property
    def label(self) -> str:
        return f"{self.source or '?'} -> {self.target}"

    # ---- symbols -----------------------------------------------------------------
    def lookup(self, old: str) -> Move | None:
        if old in self.extra_moves:
            return Move(old=old, new=self.extra_moves[old] or None, status="custom")
        entry = self.raw["symbols"].get(old)
        note = self.raw["notes"].get(old)
        if entry and self.target in entry:
            status, new, internal = entry[self.target]
            return Move(old=old, new=new, status=status, internal=bool(internal), note=note)
        module, _, name = old.rpartition(".")
        moves = self.raw["module_moves"].get(module, {})
        if self.target in moves:
            target_mod = moves[self.target]
            # Prefer a public airflow.sdk export with the same name when one exists.
            if name in self.sdk_all and module.endswith("trigger_rule"):
                return Move(old=old, new=f"airflow.sdk.{name}", status="deprecated")
            internal = any(m in target_mod for m in ("._internal", ".execution_time.", "._shared"))
            return Move(old=old, new=f"{target_mod}.{name}", status="deprecated", internal=internal)
        return None

    def is_known_module(self, module: str) -> bool:
        prefix = module + "."
        return any(k.startswith(prefix) for k in self.raw["symbols"]) or module in self.raw["module_moves"]

    def removed_module(self, module: str) -> bool:
        """True if every probed symbol under this module is removed on the target."""
        prefix = module + "."
        seen = False
        for k, per in self.raw["symbols"].items():
            if k.startswith(prefix) and k.count(".") == module.count(".") + 1 and self.target in per:
                seen = True
                if per[self.target][0] != "removed":
                    return False
        return seen

    def all_old_paths(self) -> list[str]:
        return list(self.raw["symbols"]) + list(self.extra_moves)

    # ---- facts -----------------------------------------------------------------
    @property
    def sdk_all(self) -> set[str]:
        return set(self.raw["sdk_all"][self.target])

    def sdk_has(self, name: str) -> bool:
        return name in self.sdk_all

    @property
    def context_keys(self) -> set[str]:
        return set(self.raw["context_keys"][self.target])

    @property
    def removed_context_keys(self) -> set[str]:
        return set(self.raw["removed_context_keys"][self.target])

    @property
    def dag_params(self) -> set[str]:
        return set(self.raw["dag_params"][self.target])

    @property
    def dag_positional(self) -> list[str]:
        return list(self.raw["dag_positional"][self.target])

    @property
    def operator_params(self) -> set[str]:
        return set(self.raw["operator_params"][self.target])

    @property
    def trigger_dagrun_params(self) -> set[str]:
        return set(self.raw["trigger_dagrun_params"][self.target])

    @property
    def plugin_attrs(self) -> set[str]:
        return set(self.raw["plugin_attrs"][self.target])

    @property
    def runtime_ti_methods(self) -> set[str]:
        return set(self.raw["runtime_ti_methods"][self.target])

    @property
    def runtime_ti_signatures(self) -> dict[str, str]:
        return dict(self.raw["runtime_ti_signatures"][self.target])

    @property
    def config_defaults(self) -> dict[str, str]:
        return dict(self.raw["config_defaults"][self.target])

    @property
    def af2(self) -> dict:
        return self.raw["af2"]

    def timetable_import(self, name: str) -> tuple[str, str]:
        """Where to import a timetable class from on this target."""
        if self.sdk_has(name):
            return "airflow.sdk", name
        legacy = {
            "CronDataIntervalTimetable": "airflow.timetables.interval",
            "DeltaDataIntervalTimetable": "airflow.timetables.interval",
            "CronTriggerTimetable": "airflow.timetables.trigger",
        }
        return legacy[name], name
