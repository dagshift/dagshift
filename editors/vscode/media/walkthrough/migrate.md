## Preview and apply

* **One file**: the CodeLens at the top of the file, or *dagshift: Preview Upgrade Changes*, opens a diff.
  *Apply* writes it into the editor (undo works as usual).
* **Everything**: *dagshift: Upgrade Workspace...* asks where the upgraded files go:
  * *Edit the files in place*: VS Code's refactor preview lists every file. Untick what you want to keep,
    then *Apply*.
  * *Write an upgraded copy*: your files stay as they are; the copy goes next to the project (or a folder you
    pick) with `AIRFLOW_UPGRADE_REPORT.md`. *Compare Changes* shows originals and copy side by side.
* **Always the same answer**: set `dagshift.upgrade.output` to `inPlace` or `folder`.
* **On save** (optional): add `"editor.codeActionsOnSave": { "source.fixAll.dagshift": "explicit" }`.
