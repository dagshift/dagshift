# Knowledge base and docs tools

`src/dagshift/_data/kb.json` and `docs/rules.md` are generated, never edited by hand.

| file | role |
|---|---|
| `candidates.py` | curated legacy paths and their preferred homes (unverified input) |
| `harvest_af2.py` | reads Airflow 2.x's own legacy-path tables and checks provider targets in current provider wheels; writes `af2_moves.json` |
| `probe_airflow.py` | runs inside one Airflow environment (2.x or 3.x) and records what imports, what warns, SDK exports, context keys, DAG/operator/`Variable.get` parameters and plugin attributes |
| `build_kb.py` | runs the probe in every target environment and folds the facts into `kb.json` |
| `gen_rules_doc.py` | writes `docs/rules.md`, running every example through the engine (`--check` in CI) |

## Refresh for a new Airflow release

```bash
# 1. one venv per target minor (uv makes this fast)
for v in 2.7.3 2.8.4 2.9.3 2.10.5 2.11.0; do
  uv venv -p 3.11 af-$v
  VIRTUAL_ENV=af-$v uv pip install "apache-airflow==$v" \
    -c https://raw.githubusercontent.com/apache/airflow/constraints-$v/constraints-3.11.txt
done
for v in 3.0.6 3.1.8 3.2.2 3.3.1; do
  uv venv -p 3.12 af-$v
  VIRTUAL_ENV=af-$v uv pip install "apache-airflow-core==$v" apache-airflow-task-sdk \
    apache-airflow-providers-standard apache-airflow-providers-common-sql apache-airflow-providers-smtp \
    -c https://raw.githubusercontent.com/apache/airflow/constraints-$v/constraints-3.12.txt
done

# 2. Airflow 2 legacy tables (AST only, nothing installed)
pip download --no-deps --only-binary=:all: --python-version 3.12 apache-airflow==2.11.0 -d af2 && unzip -q af2/*.whl -d af2/x
python tools/harvest_af2.py af2/x/airflow provider-wheels

# 3. build (one argument per target minor)
python tools/build_kb.py 2.7=af-2.7.3/bin/python 2.8=af-2.8.4/bin/python 2.9=af-2.9.3/bin/python \
  2.10=af-2.10.5/bin/python 2.11=af-2.11.0/bin/python 3.0=af-3.0.6/bin/python 3.1=af-3.1.8/bin/python \
  3.2=af-3.2.2/bin/python 3.3=af-3.3.1/bin/python

# 4. docs
python tools/gen_rules_doc.py

# 5. prove it
DAGSHIFT_AIRFLOW_PYTHONS="2.10=af-2.10.5/bin/python,3.0=af-3.0.6/bin/python,3.3=af-3.3.1/bin/python" \
DAGSHIFT_AF2_EXAMPLES=af2/x/airflow/example_dags pytest
```

Add a new minor by adding `3.4=...` to step 3; `--target 3.4` then works everywhere, including the
extension's target list (add `"3.4"` to the `dagshift.target` enum in `editors/vscode/package.json`).
