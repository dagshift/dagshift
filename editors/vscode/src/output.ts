/** Where an upgraded copy goes. Pure Node (no vscode import) so it can be unit-tested. */
import * as fs from "node:fs";
import * as path from "node:path";

export const MARKER = ".dagshift-upgrade.json";

/** dagshift.upgrade.outputFolder with its variables filled in; relative paths sit next to `base`. */
export function outputFolderFor(base: string, source: string | null | undefined, target: string, template?: string): string {
  const raw = (template?.trim() || "${folderName}-airflow-${target}")
    .replace(/\$\{folderName\}/g, path.basename(base))
    .replace(/\$\{target\}/g, target)
    .replace(/\$\{source\}/g, source ?? "unknown");
  const expanded = raw.startsWith("~") ? path.join(process.env.HOME ?? process.env.USERPROFILE ?? "", raw.slice(1)) : raw;
  return path.isAbsolute(expanded) ? path.normalize(expanded) : path.join(path.dirname(base), expanded);
}

/** Short, readable form of an output folder: relative to the upgraded folder when close by. */
export function displayFolder(outDir: string, base: string): string {
  const rel = path.relative(base, outDir);
  if (rel && !rel.startsWith(`..${path.sep}..`) && !path.isAbsolute(rel)) {
    return rel.split(path.sep).join("/");
  }
  const home = process.env.HOME ?? process.env.USERPROFILE;
  return home && outDir.startsWith(home + path.sep) ? `~${outDir.slice(home.length)}` : outDir;
}

export function folderState(dir: string): "missing" | "empty" | "earlier-copy" | "other" {
  try {
    const names = fs.readdirSync(dir);
    if (!names.length) {
      return "empty";
    }
    return names.includes(MARKER) ? "earlier-copy" : "other";
  } catch {
    return "missing";
  }
}


/**
 * True when `file` sits in an upgraded copy below `root` (or anywhere, for files outside a workspace).
 * Such files are already upgraded; linting them would count their findings twice. A workspace whose
 * root is itself an upgraded copy is linted normally.
 */
export function insideUpgradedCopy(file: string, root?: string): boolean {
  let dir = path.dirname(file);
  for (;;) {
    if (root && (dir === root || !(dir + path.sep).startsWith(root + path.sep))) {
      return false;
    }
    if (fs.existsSync(path.join(dir, MARKER))) {
      return true;
    }
    const up = path.dirname(dir);
    if (up === dir) {
      return false;
    }
    dir = up;
  }
}
