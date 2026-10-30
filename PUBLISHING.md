# Publishing dagshift

Two things ship from this repository, from one tag:

| what | where | how it authenticates |
|---|---|---|
| `dagshift` engine and CLI | PyPI | trusted publishing (GitHub OIDC), no token |
| `dagshift.dagshift` VS Code extension | VS Code Marketplace | trusted publishing (GitHub OIDC), no token |
| the same extension | Open VSX (Cursor, Windsurf, VSCodium, code-server) | `OVSX_PAT` secret |

The extension carries its own copy of the engine wheel, so it works without PyPI. PyPI is still published
first so `pip install dagshift` works the moment the extension is live.

Azure DevOps personal access tokens are not used: global PATs stop working on December 1, 2026, and the
Marketplace now supports trusted publishing from GitHub Actions instead.

## One-time setup

### 1. GitHub repository

The code lives in `dagshift/dagshift` (the `dagshift` organization). In the repository's **Settings >
Environments**, create two environments:

* `pypi`
* `marketplace`

Adding yourself as a required reviewer on both makes every release wait for one click before anything is
published. Optional, but recommended.

### 2. PyPI

1. Sign in at pypi.org (an account with two-factor authentication).
2. Go to **Your account > Publishing > Add a new pending publisher > GitHub** and enter:
   * PyPI project name: `dagshift`
   * Owner: `dagshift`
   * Repository name: `dagshift`
   * Workflow name: `release.yml`
   * Environment name: `pypi`
3. The first successful release creates the project and turns the pending publisher into a normal one.

### 3. VS Code Marketplace

1. Sign in at marketplace.visualstudio.com/manage with a personal Microsoft account (not a school or work
   account, which can be switched off later) and **Create publisher** with ID `dagshift`. It must match
   `"publisher"` in `editors/vscode/package.json`, and it can never be changed.
2. Open the publisher, select **Trusted Publishing > Create**, and enter:
   * Policy name: `Release workflow`
   * Repository owner: `dagshift`
   * Repository: `dagshift`
   * Workflow: `release.yml`
   * Environment: `marketplace`
3. If the very first publish is rejected because the extension does not exist yet, download the `dist`
   artifact from that workflow run (the run's summary page, **Artifacts**), and upload the `.vsix` in it once
   by hand on the same page (**New extension > Visual Studio Code**). Then re-run the failed jobs. Every later
   release goes through the workflow.

### 4. Open VSX (optional; the step is skipped while the secret is missing)

1. Sign in at open-vsx.org with GitHub, link an Eclipse account and accept the Publisher Agreement.
2. Create an access token under **Settings > Access Tokens**.
3. Create the namespace once: `npx ovsx create-namespace dagshift -p <token>`
4. Add the token as secret `OVSX_PAT` in the `marketplace` environment.

## Every release

1. Set the same version in `pyproject.toml` and `editors/vscode/package.json` (the workflow refuses a
   mismatch), and add a section to `editors/vscode/CHANGELOG.md`.
2. Dry run: **Actions > release > Run workflow** with *Publish* unticked. It runs every test, builds the
   wheel, sdist and VSIX, and uploads them as the `dist` artifact. Install the VSIX from there to check it.
3. Tag and push:

   ```bash
   git commit -am "Release 0.1.0"
   git tag v0.1.0
   git push origin main v0.1.0
   ```

4. The workflow publishes to PyPI, then the Marketplace and Open VSX, then creates a GitHub release with the
   wheel, sdist and VSIX attached.
5. Check: `pip install dagshift==0.1.0 && dagshift targets`, and install *dagshift: Airflow Upgrade Assistant*
   from the Extensions view. The Marketplace can take a few minutes to list a new version.

## When a new Airflow version comes out

See `tools/README.md`: build a venv for it, rerun `tools/build_kb.py` and `tools/gen_rules_doc.py`, add the
version to the `dagshift.target` list in `editors/vscode/package.json`, run the tests, release.
