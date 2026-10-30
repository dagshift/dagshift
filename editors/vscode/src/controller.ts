/** Decides when to scan and re-check, and keeps context keys in sync. */
import * as vscode from "vscode";
import { affectsResults, engineOptions, explicitSource, setting } from "./config";
import type { CheckResult, FileReport } from "./engineClient";
import type { Engine } from "./engine";
import { folderUsesAirflow, isCandidate, textUsesAirflow } from "./detect";
import { upgradeLabel, type FindingsModel } from "./model";
import type { Diagnostics, StatusBar } from "./views";
import { insideUpgradedCopy } from "./output";

export class Controller implements vscode.Disposable {
  private readonly detected = new Set<string>();
  private readonly timers = new Map<string, NodeJS.Timeout>();
  private readonly requested = new Map<string, number>();
  private readonly scansInFlight = new Map<string, Promise<CheckResult | undefined>>();
  private readonly disposables: vscode.Disposable[] = [];
  private scanning = 0;

  constructor(
    private readonly ctx: vscode.ExtensionContext,
    private readonly engine: Engine,
    private readonly model: FindingsModel,
    private readonly diagnostics: Diagnostics,
    private readonly status: StatusBar,
    private readonly log: vscode.LogOutputChannel,
  ) {
    this.disposables.push(
      model.onDidChange(() => {
        this.diagnostics.render();
        this.updateContext();
      }),
      vscode.workspace.onDidOpenTextDocument((d) => this.scheduleLint(d, 0)),
      vscode.workspace.onDidChangeTextDocument((e) => {
        if (e.contentChanges.length && (setting<boolean>("lintOnType", e.document.uri) ?? true)) {
          this.scheduleLint(e.document, setting<number>("lintDelay", e.document.uri) ?? 700);
        }
      }),
      vscode.workspace.onDidSaveTextDocument((d) => this.scheduleLint(d, 0)),
      vscode.workspace.onDidCloseTextDocument((d) => {
        // the buffer may have held unsaved edits: fall back to what is on disk
        if (d.isDirty && this.model.get(d.uri)) {
          void this.lintUri(d.uri);
        }
      }),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (affectsResults(e)) {
          void this.rescanAll();
        } else if (e.affectsConfiguration("dagshift.showInfo")) {
          this.diagnostics.render();
        } else if (e.affectsConfiguration("dagshift.interpreter")) {
          void vscode.commands.executeCommand("dagshift.restartEngine");
        }
      }),
      vscode.workspace.onDidChangeWorkspaceFolders((e) => {
        e.removed.forEach((f) => {
          this.model.clearFolder(f);
          this.detected.delete(f.uri.toString());
        });
        e.added.forEach((f) => void this.detectAndScan(f, false));
      }),
    );
    const watcher = vscode.workspace.createFileSystemWatcher("**/*");
    this.disposables.push(
      watcher,
      watcher.onDidCreate((u) => this.onDiskChange(u)),
      watcher.onDidChange((u) => this.onDiskChange(u)),
      watcher.onDidDelete((u) => this.model.remove(u)),
    );
  }

  enabled(scope?: vscode.Uri): boolean {
    return setting<boolean>("enable", scope) ?? true;
  }

  /** Called once on activation. */
  async start(): Promise<void> {
    this.updateContext();
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      void this.detectAndScan(folder, false);
    }
    for (const doc of vscode.workspace.textDocuments) {
      this.scheduleLint(doc, 0);
    }
  }

  private async detectAndScan(folder: vscode.WorkspaceFolder, force: boolean): Promise<void> {
    if (!this.enabled(folder.uri)) {
      return;
    }
    if (!force && !(await folderUsesAirflow(folder))) {
      this.log.info(`No Airflow code found in ${folder.name}; dagshift stays idle there.`);
      return;
    }
    this.markDetected(folder);
    if (force || (setting<boolean>("scanOnOpen", folder.uri) ?? true)) {
      const result = await this.scan(folder);
      if (result) {
        this.maybeNotify(folder, result);
      }
    }
  }

  private markDetected(folder: vscode.WorkspaceFolder): void {
    if (!this.detected.has(folder.uri.toString())) {
      this.detected.add(folder.uri.toString());
      this.status.setDetected(true);
      void vscode.commands.executeCommand("setContext", "dagshift.airflowDetected", true);
    }
  }

  /** Full scan of one folder from disk. */
  scan(folder: vscode.WorkspaceFolder): Promise<CheckResult | undefined> {
    const key = folder.uri.toString();
    const inflight = this.scansInFlight.get(key);
    if (inflight) {
      return inflight;
    }
    const run = this.doScan(folder).finally(() => this.scansInFlight.delete(key));
    this.scansInFlight.set(key, run);
    return run;
  }

  private async doScan(folder: vscode.WorkspaceFolder): Promise<CheckResult | undefined> {
    this.setScanning(+1);
    try {
      if (!this.engine.current) {
        this.status.setState("setup");
      }
      const client = await this.engine.get();
      this.status.setState("scanning");
      const started = Date.now();
      const result = await client.check(folder.uri.fsPath, engineOptions(folder.uri));
      this.log.info(
        `Scanned ${folder.name} in ${((Date.now() - started) / 1000).toFixed(1)}s for Airflow ` +
          `${result.source ?? "?"} -> ${result.target}: ` +
          `${result.summary.files_changed} files to migrate, ${result.summary.fixed} fixes, ` +
          `${result.summary.manual} manual, ${result.summary.warn} warn, ${result.summary.info} info`,
      );
      this.model.setFolder(folder, result);
      // the scan read files from disk; unsaved editors keep their own (newer) state
      for (const doc of vscode.workspace.textDocuments) {
        if (doc.isDirty && vscode.workspace.getWorkspaceFolder(doc.uri) === folder) {
          this.scheduleLint(doc, 0);
        }
      }
      this.status.setState("idle");
      return result;
    } catch (err) {
      this.status.setState("error");
      this.engine.reportError(err, `scan of ${folder.name}`);
      return undefined;
    } finally {
      this.setScanning(-1);
    }
  }

  async rescanAll(force = false): Promise<void> {
    const folders = vscode.workspace.workspaceFolders ?? [];
    for (const folder of folders) {
      if (!this.enabled(folder.uri)) {
        this.model.clearFolder(folder);
        continue;
      }
      if (force || this.detected.has(folder.uri.toString())) {
        await this.detectAndScan(folder, true);
      }
    }
  }

  private setScanning(delta: number): void {
    this.scanning += delta;
    void vscode.commands.executeCommand("setContext", "dagshift.scanning", this.scanning > 0);
  }

  // -- single files -------------------------------------------------------------------
  private shouldLint(uri: vscode.Uri, text?: string): boolean {
    if (!isCandidate(uri) || !this.enabled(uri)) {
      return false;
    }
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (insideUpgradedCopy(uri.fsPath, folder?.uri.fsPath)) {
      return false; // already upgraded (for example the right side of "Compare Changes")
    }
    if (folder && this.detected.has(folder.uri.toString())) {
      return true;
    }
    if (text !== undefined && textUsesAirflow(text)) {
      if (folder) {
        // Airflow code in a folder detection missed (large monorepo): adopt it.
        void this.detectAndScan(folder, true);
      }
      return true;
    }
    return false;
  }

  scheduleLint(doc: vscode.TextDocument, delay: number): void {
    if (doc.uri.scheme !== "file" || !this.shouldLint(doc.uri, doc.getText())) {
      return;
    }
    const key = doc.uri.toString();
    clearTimeout(this.timers.get(key));
    this.timers.set(
      key,
      setTimeout(() => {
        this.timers.delete(key);
        void this.lintDocument(doc);
      }, delay),
    );
  }

  private async lintDocument(doc: vscode.TextDocument): Promise<void> {
    if (doc.isClosed) {
      return;
    }
    const key = doc.uri.toString();
    const version = doc.version;
    this.requested.set(key, version);
    const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
    try {
      const client = await this.engine.get();
      const report = await client.migrateDocument(doc.uri.fsPath, doc.getText(), folder?.uri.fsPath, engineOptions(doc.uri));
      if (this.requested.get(key) !== version || doc.isClosed) {
        return; // a newer edit is on its way
      }
      this.model.setFile(doc.uri, folder, report);
    } catch (err) {
      this.engine.reportError(err, `checking ${vscode.workspace.asRelativePath(doc.uri)}`);
    }
  }

  private async lintUri(uri: vscode.Uri): Promise<void> {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    try {
      const client = await this.engine.get();
      const report: FileReport = await client.migrateDocument(uri.fsPath, undefined, folder?.uri.fsPath, engineOptions(uri));
      this.model.setFile(uri, folder, report);
    } catch (err) {
      this.log.warn(`re-check of ${uri.fsPath} failed: ${(err as Error).message}`);
    }
  }

  private onDiskChange(uri: vscode.Uri): void {
    if (!isCandidate(uri)) {
      return;
    }
    const open = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());
    if (open) {
      return; // editor events cover it
    }
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (!folder || !this.detected.has(folder.uri.toString())) {
      return;
    }
    const key = `disk:${uri.toString()}`;
    clearTimeout(this.timers.get(key));
    this.timers.set(
      key,
      setTimeout(() => {
        this.timers.delete(key);
        void this.lintUri(uri);
      }, 500),
    );
  }

  // -- first-run notification ----------------------------------------------------------
  private maybeNotify(folder: vscode.WorkspaceFolder, result: CheckResult): void {
    const s = result.summary;
    const where = (vscode.workspace.workspaceFolders?.length ?? 0) > 1 ? ` (${folder.name})` : "";
    const upgrade = this.model.upgradeFor(folder);
    // The current version decides which rules apply: ask once when it could not be found.
    if (!explicitSource(folder.uri) && result.detection?.confidence === "unknown") {
      const key = `dagshift.askedSource:${folder.uri.toString()}`;
      if (!this.ctx.workspaceState.get<boolean>(key)) {
        void this.ctx.workspaceState.update(key, true);
        void vscode.window
          .showWarningMessage(
            `dagshift could not find which Airflow version this project${where} runs today. If your Composer or ` +
              "MWAA environment is defined in another repository, point dagshift at it, or pick the version.",
            "Select Current Version...",
          )
          .then((choice) => {
            if (choice) {
              void vscode.commands.executeCommand("dagshift.selectSource", folder);
            }
          });
        return;
      }
    }
    if (!(setting<boolean>("showSummaryNotification") ?? true) || s.fixed + s.manual + s.warn === 0) {
      return;
    }
    const key = `dagshift.notified:${folder.uri.toString()}`;
    if (this.ctx.workspaceState.get<boolean>(key)) {
      return;
    }
    void this.ctx.workspaceState.update(key, true);
    const how = result.detection
      ? result.detection.confidence === "pinned"
        ? ` (current version from ${result.detection.evidence[0]?.file})`
        : " (current version inferred from the code)"
      : "";
    const msg =
      `dagshift${where}: ${upgradeLabel(upgrade)}${how}. ${s.files_changed} file${s.files_changed === 1 ? "" : "s"} ` +
      `can be updated automatically (${s.fixed} fixes)` +
      (s.manual ? `, and ${s.manual} item${s.manual === 1 ? " needs" : "s need"} your decision.` : ".");
    void vscode.window
      .showInformationMessage(msg, "Review Findings", "Change Versions...", "Upgrade Workspace...")
      .then((choice) => {
        if (choice === "Review Findings") {
          void vscode.commands.executeCommand("dagshift.findings.focus");
        } else if (choice === "Change Versions...") {
          void vscode.commands.executeCommand("dagshift.selectVersions", folder);
        } else if (choice === "Upgrade Workspace...") {
          void vscode.commands.executeCommand("dagshift.migrateWorkspace");
        }
      });
  }

  private updateContext(): void {
    const t = this.model.totals();
    // `resourcePath` in when-clauses is the file-system path (backslashes and a drive letter on Windows), so
    // list both forms; with only uri.path, the Preview buttons never showed on Windows.
    const changed = this.model.changedEntries().flatMap((e) => [e.uri.path, e.uri.fsPath]);
    void vscode.commands.executeCommand("setContext", "dagshift.hasFindings", t.fixable + t.manual + t.warn + t.info > 0);
    void vscode.commands.executeCommand("setContext", "dagshift.hasChanges", changed.length > 0);
    void vscode.commands.executeCommand("setContext", "dagshift.changedFiles", changed);
  }

  dispose(): void {
    this.timers.forEach((t) => clearTimeout(t));
    this.disposables.forEach((d) => d.dispose());
  }
}
