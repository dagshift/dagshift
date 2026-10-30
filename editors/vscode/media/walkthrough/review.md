## Review

| Mark | Meaning |
|---|---|
| Red | Breaks on the target version and has no safe automatic rewrite (for Airflow 3: metadata DB access, SubDAGs, Flask plugins). Hover for the recipe. |
| Yellow | dagshift can fix it, or it still imports but behaves differently (for Airflow 3: cron timetables, parse-time values). |
| Blue | Notes worth knowing before cutover. |

Nothing is changed until you preview and apply.
