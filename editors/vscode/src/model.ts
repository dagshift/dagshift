/** In-memory findings for every workspace folder. Diagnostics, the tree, CodeLens and the status
 * bar all render from here. */
import * as vscode from "vscode";
import type { CheckResult, Detection, FileReport, Finding, Summary } from "./engineClient";

/** The upgrade being planned for one workspace folder. */
export interface Upgrade {
  source: string | null; // current version (minor), null when unknown
  target: string;
  verifiedAgainst: string;
  detection: Detection | null; // null when the user chose the current version explicitly
}

export interface FileEntry {
  uri: vscode.Uri;
  folder: vscode.WorkspaceFolder | undefined;
  report: FileReport;
}

export interface Totals {
  fixable: number;
  manual: number;
  warn: number;
  info: number;
  filesChanged: number;
  files: number;
}

export class FindingsModel implements vscode.Disposable {
  private readonly files = new Map<string, FileEntry>();
  private readonly projectFindings = new Map<string, Finding[]>();
  private readonly upgrades = new Map<string, Upgrade>();
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChange = this._onDidChange.event;
  private pendingFire?: NodeJS.Timeout;

  private fire(): void {
    if (this.pendingFire) {
      return;
    }
    this.pendingFire = setTimeout(() => {
      this.pendingFire = undefined;
      this._onDidChange.fire();
    }, 30);
  }

  /** Replace everything known about one workspace folder with a fresh scan. */
  setFolder(folder: vscode.WorkspaceFolder, result: CheckResult): void {
    const prefix = folder.uri.toString();
    for (const [key, entry] of this.files) {
      if (entry.folder?.uri.toString() === prefix) {
        this.files.delete(key);
      }
    }
    for (const report of result.files) {
      const uri = vscode.Uri.file(report.path);
      this.files.set(uri.toString(), { uri, folder, report });
    }
    this.projectFindings.set(prefix, result.projectFindings);
    this.upgrades.set(prefix, {
      source: result.source,
      target: result.target,
      verifiedAgainst: result.verifiedAgainst,
      detection: result.detection,
    });
    this.fire();
  }

  /** Update one file (editor buffer or disk change). */
  setFile(uri: vscode.Uri, folder: vscode.WorkspaceFolder | undefined, report: FileReport): void {
    const key = uri.toString();
    const relevant = report.changed || report.error || report.findings.length > 0;
    const had = this.files.has(key);
    if (relevant) {
      this.files.set(key, { uri, folder, report });
      this.fire();
    } else if (had) {
      this.files.delete(key);
      this.fire();
    }
  }

  remove(uri: vscode.Uri): void {
    if (this.files.delete(uri.toString())) {
      this.fire();
    }
  }

  clearFolder(folder: vscode.WorkspaceFolder): void {
    const prefix = folder.uri.toString();
    for (const [key, entry] of this.files) {
      if (entry.folder?.uri.toString() === prefix) {
        this.files.delete(key);
      }
    }
    this.projectFindings.delete(prefix);
    this.upgrades.delete(prefix);
    this.fire();
  }

  clear(): void {
    this.files.clear();
    this.projectFindings.clear();
    this.fire();
  }

  get(uri: vscode.Uri): FileEntry | undefined {
    return this.files.get(uri.toString());
  }

  entries(): FileEntry[] {
    return [...this.files.values()].sort((a, b) => a.uri.path.localeCompare(b.uri.path));
  }

  /** Findings for a file, including project-level findings that point at it (e.g. requirements). */
  findingsFor(uri: vscode.Uri): Finding[] {
    const own = this.files.get(uri.toString())?.report.findings ?? [];
    const extra: Finding[] = [];
    for (const list of this.projectFindings.values()) {
      for (const f of list) {
        if (f.absPath && vscode.Uri.file(f.absPath).toString() === uri.toString()) {
          extra.push(f);
        }
      }
    }
    return extra.length ? [...own, ...extra] : own;
  }

  /** Project findings whose file is not otherwise in the model (shown as their own tree entries). */
  orphanProjectFindings(): { uri: vscode.Uri | undefined; finding: Finding }[] {
    const out: { uri: vscode.Uri | undefined; finding: Finding }[] = [];
    for (const list of this.projectFindings.values()) {
      for (const f of list) {
        const uri = f.absPath ? vscode.Uri.file(f.absPath) : undefined;
        if (!uri || !this.files.has(uri.toString())) {
          out.push({ uri, finding: f });
        }
      }
    }
    return out;
  }

  allUris(): vscode.Uri[] {
    const uris = new Map<string, vscode.Uri>();
    for (const e of this.files.values()) {
      uris.set(e.uri.toString(), e.uri);
    }
    for (const { uri } of this.orphanProjectFindings()) {
      if (uri) {
        uris.set(uri.toString(), uri);
      }
    }
    return [...uris.values()];
  }

  upgradeFor(folder: vscode.WorkspaceFolder | undefined): Upgrade | undefined {
    if (folder) {
      return this.upgrades.get(folder.uri.toString());
    }
    return this.upgrades.values().next().value;
  }

  totals(): Totals {
    const t: Totals = { fixable: 0, manual: 0, warn: 0, info: 0, filesChanged: 0, files: 0 };
    const count = (f: Finding) => {
      if (f.severity === "fixed") t.fixable++;
      else if (f.severity === "manual") t.manual++;
      else if (f.severity === "warn") t.warn++;
      else t.info++;
    };
    for (const e of this.files.values()) {
      t.files++;
      if (e.report.changed) t.filesChanged++;
      e.report.findings.forEach(count);
    }
    for (const list of this.projectFindings.values()) {
      list.forEach(count);
    }
    return t;
  }

  changedEntries(within?: vscode.Uri): FileEntry[] {
    const prefix = within ? within.path.replace(/\/?$/, "/") : undefined;
    return this.entries().filter((e) => e.report.changed && (!prefix || e.uri.path.startsWith(prefix) || e.uri.path === within?.path));
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}

export function summaryLine(s: Summary): string {
  return `${s.fixed} automatic fixes, ${s.manual} need you, ${s.warn} warnings`;
}

/** "2.10.5" style label for the current version, with "?" when it was only inferred. */
export function sourceLabel(u: Upgrade | undefined): string {
  if (!u?.source) {
    return "?";
  }
  const d = u.detection;
  if (d?.version && d.minor === u.source) {
    return d.confidence === "inferred" ? `${d.version}+` : d.version;
  }
  return u.source;
}

export function upgradeLabel(u: Upgrade | undefined): string {
  return u ? `Airflow ${sourceLabel(u)} → ${u.target}` : "Airflow";
}

export function detectionSummary(u: Upgrade | undefined): string {
  if (!u) {
    return "";
  }
  const d = u.detection;
  if (!d) {
    return u.source ? `Current version ${u.source} was set in dagshift.sourceVersion or dagshift.toml.` : "";
  }
  if (d.confidence === "unknown") {
    return "Current version not found: pick it, or point dagshift at the repository that defines your environment.";
  }
  const ev = d.evidence[0];
  const where = ev ? `${ev.file}:${ev.line} (${ev.kind})` : "";
  return d.confidence === "pinned"
    ? `Current version ${d.version} detected from ${where}.` + (d.conflicts.length ? ` Also found: ${d.conflicts.join(", ")}.` : "")
    : `No pinned version found; the code needs at least Airflow ${d.version} (${ev?.text ?? ""}).`;
}
