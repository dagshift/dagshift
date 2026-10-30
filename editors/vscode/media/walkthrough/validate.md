## Validate on a real install of the target

Static rules cannot see values injected at render or run time. Validation closes that gap: dagshift loads your
DAG folder with Airflow's own DagBag in a Python environment you choose, and reports import errors and
deprecation warnings in the Problems panel.

```bash
python -m venv ~/.venvs/airflow3
~/.venvs/airflow3/bin/pip install "apache-airflow==3.3.1" \
  --constraint https://raw.githubusercontent.com/apache/airflow/constraints-3.3.1/constraints-3.12.txt
```

Then point *dagshift.validate.airflowPython* at `~/.venvs/airflow3/bin/python`. DAG factories that render at
build time can set *dagshift.validate.renderCommand*; it runs in a temporary copy, never in your workspace.
