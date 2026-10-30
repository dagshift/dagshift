import datetime as dt
import textwrap
from pathlib import Path

import pytest

from dagshift import Options, migrate_source

FIXTURES = Path(__file__).parent / "fixtures"
ANCHOR = dt.date(2026, 10, 29)


def run(src: str, filename: str = "dag.py", **opts):
    options = Options(start_date=ANCHOR, **opts)
    return migrate_source(textwrap.dedent(src).lstrip("\n"), filename, options)


def rules(res, severity=None):
    return [f.rule_id for f in res.findings if severity is None or f.severity.value == severity]


@pytest.fixture
def project(tmp_path):
    import shutil

    dest = tmp_path / "project"
    shutil.copytree(FIXTURES / "project", dest)
    return dest
