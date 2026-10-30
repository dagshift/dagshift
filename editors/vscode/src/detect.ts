/** Cheap check for Airflow code in a workspace folder, so the engine only starts where it is useful. */
import * as vscode from "vscode";

const AIRFLOW_CODE = /^\s*(?:from|import)\s+airflow\b|^\s*-?\s*operator\s*:\s*['"]?airflow\./m;
const EXCLUDE = "**/{node_modules,.git,.venv,venv,__pycache__,site-packages,dist,build,.tox,.terraform}/**";
const MAX_FILES = 4000;
const MAX_BYTES = 128 * 1024;

export async function folderUsesAirflow(folder: vscode.WorkspaceFolder, token?: vscode.CancellationToken): Promise<boolean> {
  const marker = await vscode.workspace.findFiles(
    new vscode.RelativePattern(folder, "{**/airflow.cfg,**/dagshift.toml,**/.airflowignore}"),
    EXCLUDE,
    1,
    token,
  );
  if (marker.length) {
    return true;
  }
  const files = await vscode.workspace.findFiles(
    new vscode.RelativePattern(folder, "**/*.{py,j2,jinja,jinja2,tmpl,tpl,template,yaml,yml}"),
    EXCLUDE,
    MAX_FILES,
    token,
  );
  // Look where DAGs usually live first.
  const score = (u: vscode.Uri) => (/\/(dags|plugins)\//i.test(u.path) ? 0 : /dag/i.test(u.path) ? 1 : 2);
  files.sort((a, b) => score(a) - score(b));
  let found = false;
  let index = 0;
  const worker = async () => {
    while (!found && index < files.length && !token?.isCancellationRequested) {
      const uri = files[index++];
      try {
        const bytes = await vscode.workspace.fs.readFile(uri);
        const text = new TextDecoder("utf-8", { fatal: false }).decode(bytes.subarray(0, MAX_BYTES));
        if (AIRFLOW_CODE.test(text)) {
          found = true;
        }
      } catch {
        // unreadable: skip
      }
    }
  };
  await Promise.all(Array.from({ length: 16 }, worker));
  return found;
}

export function textUsesAirflow(text: string): boolean {
  return AIRFLOW_CODE.test(text);
}

/** Files the engine can say something about (mirrors dagshift.engine.classify). */
export function isCandidate(uri: vscode.Uri): boolean {
  if (uri.scheme !== "file") {
    return false;
  }
  const name = uri.path.split("/").pop() ?? "";
  const low = name.toLowerCase();
  if (/(^|\/)(node_modules|\.git|\.venv|venv|__pycache__|site-packages)\//.test(uri.path)) {
    return false;
  }
  if (name === ".airflowignore" || name === "airflow.cfg" || low.startsWith("dockerfile") || low.endsWith(".dockerfile")) {
    return true;
  }
  if (/^(requirements.*\.(txt|in)|constraints.*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile|environment\.ya?ml)$/.test(name)) {
    return true;
  }
  return /\.(py|ya?ml|json|j2|jinja|jinja2|tmpl|tpl|template|tf|tfvars|hcl)$/i.test(name);
}
