# FAQ

??? question "Does dagshift change my files without asking?"

    No. Scanning only reads. Every change goes through a diff or VS Code's refactor preview first, and you can
    choose to write an upgraded copy instead of touching the originals at all.

??? question "Does it send my code anywhere?"

    No. Everything runs locally. The only network access is the one-time `pip install` of the engine's single
    dependency (`libcst`) from your configured package index, when the extension sets itself up.

??? question "Do I need Airflow installed?"

    Not for scanning and upgrading. dagshift ships its own knowledge of every supported Airflow release. You only
    need an Airflow environment for [validation](guide/validate.md).

??? question "Which Python does it use?"

    Any Python 3.10 or newer on the machine, used once to create a private environment in VS Code's storage.
    Your project's interpreter is not touched. To choose one, set `dagshift.bootstrapPython`.

??? question "The engine setup failed. What now?"

    Run **dagshift: Show Log** to see why. Common causes:

    * No Python 3.10+ found: install one, or set `dagshift.bootstrapPython`.
    * On Debian or Ubuntu, `python3 -m venv` needs the `python3-venv` package. dagshift falls back to `uv`
      when it is installed.
    * A proxy or private package index blocks `libcst`: configure pip as you normally would (`pip.conf` or
      `PIP_INDEX_URL`), then run **dagshift: Reinstall Engine**.

??? question "Can it downgrade?"

    No. dagshift only upgrades; a target older than the current version is refused.

??? question "What about Airflow 1.10?"

    1.10 can be selected as the current version. The rules are measured on releases from 2.7 onward, so
    [validate](guide/validate.md) the result on the target Airflow before relying on it.

??? question "It detected the wrong current version."

    The detected version comes with the evidence used, shown when you click the status bar. If the real
    environment is defined elsewhere, point dagshift at it or pick the version yourself. See
    [Current and target version](guide/versions.md).

??? question "Does it work in Cursor, Windsurf or VSCodium?"

    The extension uses only standard VS Code APIs. Until it is listed on Open VSX, install the `.vsix` from the
    [GitHub releases](https://github.com/dagshift/dagshift/releases) page in those editors.

??? question "Where do I report a bug or ask for a rule?"

    Open an issue on [GitHub](https://github.com/dagshift/dagshift/issues). The output of
    **dagshift: Show Log** and a small file that reproduces the problem help a lot.
