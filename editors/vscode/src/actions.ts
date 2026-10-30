/** User commands: preview, apply, migrate, report, target, validate, recipes. */
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { engineOptions, explicitSource, setting, sourcePaths } from "./config";
import type { Controller } from "./controller";
import type { Engine } from "./engine";
import type { Finding, UpgradedFile, ValidateResult } from "./engineClient";
import { detectionSummary, sourceLabel, upgradeLabel, type FindingsModel } from "./model";
import { recipeMarkdown, severityLabel } from "./presentation";
import { displayFolder, folderState, outputFolderFor } from "./output";
import { fullRange } from "./views";

export const PREVIEW_SCHEME = "dagshift-migrated";

function vtuple(v: string): [number, number] {
  const m = /^(\d+)(?:\.(\d+))?/.exec(v.trim());
  return m ? [Number(m[1]), Number(m[2] ?? 0)] : [0, 0];
}

export function cmpVersion(a: string, b: string): number {
  const [x, y] = [vtuple(a), vtuple(b)];
  return x[0] - y[0] || x[1] - y[1];
}

function minorOf(v: string): string {
  const [a, b] = vtuple(v);
  return `${a}.${b}`;
}

/** Read-only documents holding migrated text, shown on the right side of diffs. */
export class MigratedContent implements vscode.TextDocumentContentProvider {
  private readonly texts = new Map<string, string>();
  private readonly _onDidChange = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this._onDidChange.event;

  uriFor(original: vscode.Uri): vscode.Uri {
    return vscode.Uri.from({ scheme: PREVIEW_SCHEME, path: original.path, query: original.toString() });
  }

  set(original: vscode.Uri, text: string): vscode.Uri {
    const uri = this.uriFor(original);
    this.texts.set(uri.toString(), text);
    this._onDidChange.fire(uri);
    return uri;
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.texts.get(uri.toString()) ?? "";
  }
}

type UriArg = vscode.Uri | string | { entry?: { uri: vscode.Uri } } | undefined;

function toUri(arg: UriArg): vscode.Uri | undefined {
  if (!arg) {
    const active = vscode.window.activeTextEditor?.document.uri;
    if (active?.scheme === PREVIEW_SCHEME) {
      return vscode.Uri.parse(active.query);
    }
    return active;
  }
  if (arg instanceof vscode.Uri) {
    return arg;
  }
  if (typeof arg === "string") {
    return vscode.Uri.parse(arg);
  }
  return arg.entry?.uri;
}

async function pickFolder(model: FindingsModel): Promise<vscode.WorkspaceFolder | undefined> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (folders.length <= 1) {
    return folders[0];
  }
  const withFindings = folders.filter((f) => model.entries().some((e) => e.folder === f));
  if (withFindings.length === 1) {
    return withFindings[0];
  }
  return vscode.window.showWorkspaceFolderPick({ placeHolder: "Which workspace folder?" });
}

function storageDir(ctx: vscode.ExtensionContext): string {
  const dir = (ctx.storageUri ?? ctx.globalStorageUri).fsPath;
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

const COPY_STATE = "dagshift.lastUpgradedCopy";
interface UpgradedCopy {
  base: string;
  outDir: string;
  source: string | null;
  target: string;
  changed: UpgradedFile[];
  report: string | null;
}

type Destination = { kind: "inPlace" } | { kind: "copy"; outDir: string };

export function registerActions(
  ctx: vscode.ExtensionContext,
  engine: Engine,
  model: FindingsModel,
  controller: Controller,
  content: MigratedContent,
  log: vscode.LogOutputChannel,
): void {
  const validateDiagnostics = vscode.languages.createDiagnosticCollection("dagshift-validate");
  ctx.subscriptions.push(validateDiagnostics);

  const migrateText = async (doc: vscode.TextDocument): Promise<string | undefined> => {
    const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
    const client = await engine.get();
    const res = await client.migrateDocument(doc.uri.fsPath, doc.getText(), folder?.uri.fsPath, engineOptions(doc.uri));
    model.setFile(doc.uri, folder, res);
    return res.changed && res.migrated ? res.migrated : undefined;
  };

  const register = (id: string, fn: (...args: never[]) => unknown) =>
    ctx.subscriptions.push(
      vscode.commands.registerCommand(id, async (...args: never[]) => {
        try {
          return await fn(...args);
        } catch (err) {
          engine.reportError(err, id);
          return undefined;
        }
      }),
    );

  // ---- preview / apply -------------------------------------------------------------
  register("dagshift.previewFile", async (arg: UriArg) => {
    const uri = toUri(arg);
    if (!uri) {
      return;
    }
    const doc = await vscode.workspace.openTextDocument(uri);
    const migrated = await migrateText(doc);
    if (migrated === undefined) {
      void vscode.window.showInformationMessage(`dagshift: ${path.basename(uri.fsPath)} needs no automatic changes for this upgrade.`);
      return;
    }
    const right = content.set(uri, migrated);
    const label = upgradeLabel(model.upgradeFor(vscode.workspace.getWorkspaceFolder(uri)));
    await vscode.commands.executeCommand("vscode.diff", uri, right, `${path.basename(uri.fsPath)} (${label})`, {
      preview: true,
    });
  });

  register("dagshift.applyFile", async (arg: UriArg) => {
    const uri = toUri(arg);
    if (!uri) {
      return;
    }
    const doc = await vscode.workspace.openTextDocument(uri);
    const migrated = await migrateText(doc);
    if (migrated === undefined) {
      void vscode.window.showInformationMessage(`dagshift: ${path.basename(uri.fsPath)} needs no automatic changes.`);
      return;
    }
    const edit = new vscode.WorkspaceEdit();
    edit.replace(uri, fullRange(doc), migrated);
    await vscode.workspace.applyEdit(edit);
  });

  register("dagshift.applyFromPreview", async () => {
    const active = vscode.window.activeTextEditor?.document.uri;
    if (active?.scheme !== PREVIEW_SCHEME) {
      return;
    }
    const original = vscode.Uri.parse(active.query);
    await vscode.commands.executeCommand("workbench.action.closeActiveEditor");
    await vscode.commands.executeCommand("dagshift.applyFile", original);
    await vscode.window.showTextDocument(original);
  });

  // ---- edit in place, through the refactor preview ------------------------------------
  const migrateMany = async (within?: vscode.Uri) => {
    const entries = model.changedEntries(within);
    if (!entries.length) {
      void vscode.window.showInformationMessage("dagshift: nothing to migrate automatically here.");
      return;
    }
    const edit = new vscode.WorkspaceEdit();
    let files = 0;
    let fixes = 0;
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "dagshift: preparing upgrade changes" },
      async (progress) => {
        const client = await engine.get();
        const byFolder = new Map<string, typeof entries>();
        for (const e of entries) {
          const key = e.folder?.uri.toString() ?? "";
          byFolder.set(key, [...(byFolder.get(key) ?? []), e]);
        }
        for (const group of byFolder.values()) {
          const folder = group[0].folder;
          const docs = await Promise.all(group.map((e) => vscode.workspace.openTextDocument(e.uri)));
          progress.report({ message: `${docs.length} files${folder ? ` in ${folder.name}` : ""}` });
          const res = await client.migrateFiles(
            folder?.uri.fsPath ?? path.dirname(docs[0].uri.fsPath),
            docs.map((d) => ({ path: d.uri.fsPath, text: d.getText() })),
            engineOptions(folder?.uri),
          );
          res.files.forEach((r, i) => {
            const doc = docs[i];
            model.setFile(doc.uri, folder, r);
            if (!r.changed || !r.migrated) {
              return;
            }
            const n = r.findings.filter((f) => f.severity === "fixed").length;
            files++;
            fixes += n;
            edit.replace(doc.uri, fullRange(doc), r.migrated, {
              label: `${vscode.workspace.asRelativePath(doc.uri)}`,
              description: `${n} fix${n === 1 ? "" : "es"}`,
              needsConfirmation: false,
              iconPath: new vscode.ThemeIcon("arrow-up"),
            });
          });
          // An optional extra, unticked by default. Because it needs confirmation, VS Code opens the
          // refactor preview for the whole edit; the file changes above start ticked.
          if (folder) {
            const report = await client.report(folder.uri.fsPath, "md", engineOptions(folder.uri));
            edit.createFile(vscode.Uri.joinPath(folder.uri, "AIRFLOW_UPGRADE_REPORT.md"), {
              overwrite: true,
              contents: Buffer.from(report.text, "utf8"),
            }, {
              label: "Also write AIRFLOW_UPGRADE_REPORT.md",
              description: "optional: what changed and what still needs you",
              needsConfirmation: true,
              iconPath: new vscode.ThemeIcon("book"),
            });
          }
        }
      },
    );
    if (!files) {
      void vscode.window.showInformationMessage("dagshift: nothing to migrate automatically here.");
      return;
    }
    log.info(`Proposing ${fixes} fixes in ${files} files (refactor preview)`);
    const applied = await vscode.workspace.applyEdit(edit, { isRefactoring: true });
    if (applied) {
      const choice = await vscode.window.showInformationMessage(
        "dagshift applied the upgrade changes. Items marked as needing you are still in the Problems panel. " +
          "Validate the result on a real install of the target Airflow?",
        "Validate DAGs",
        "Show Report",
      );
      if (choice === "Validate DAGs") {
        await vscode.commands.executeCommand("dagshift.validate");
      } else if (choice === "Show Report") {
        await vscode.commands.executeCommand("dagshift.showReport");
      }
    }
  };

  // ---- or write an upgraded copy and leave the originals alone ------------------------
  const setCopy = (copy: UpgradedCopy | undefined) => {
    void ctx.workspaceState.update(COPY_STATE, copy);
    void vscode.commands.executeCommand("setContext", "dagshift.hasUpgradedCopy", Boolean(copy));
  };
  const lastCopy = () => ctx.workspaceState.get<UpgradedCopy>(COPY_STATE);
  void vscode.commands.executeCommand("setContext", "dagshift.hasUpgradedCopy", Boolean(lastCopy()));

  const upgradeBase = async (within?: vscode.Uri) => {
    const folder = within ? vscode.workspace.getWorkspaceFolder(within) : await pickFolder(model);
    const base = within ?? folder?.uri;
    return base ? { folder, base } : undefined;
  };

  const askFolder = async (base: string, suggested: string): Promise<string | undefined> => {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: false,
      defaultUri: vscode.Uri.file(path.dirname(base)),
      openLabel: "Write Upgraded Copy Here",
      title: "Folder for the upgraded copy (an empty folder, or one with an earlier dagshift copy)",
    });
    if (!picked?.[0]) {
      return undefined;
    }
    const dir = picked[0].fsPath;
    // a folder that already holds other things gets the copy in a sub-folder instead
    return folderState(dir) === "other" ? path.join(dir, path.basename(suggested)) : dir;
  };

  const chooseDestination = async (
    base: vscode.Uri,
    folder: vscode.WorkspaceFolder | undefined,
    forceCopy: boolean,
  ): Promise<Destination | undefined> => {
    const upgrade = model.upgradeFor(folder);
    const target = upgrade?.target ?? setting<string>("target", base) ?? "latest";
    const suggested = outputFolderFor(base.fsPath, upgrade?.source, target, setting<string>("upgrade.outputFolder", base));
    const mode = forceCopy ? "folder" : setting<string>("upgrade.output", base) ?? "ask";
    if (mode === "inPlace") {
      return { kind: "inPlace" };
    }
    if (mode === "folder" && !forceCopy) {
      return { kind: "copy", outDir: suggested };
    }
    type Item = vscode.QuickPickItem & { pick: "inPlace" | "suggested" | "browse" };
    const items: Item[] = [];
    if (!forceCopy) {
      items.push({
        label: "$(edit) Edit the files in place",
        detail: "Review every change in VS Code's refactor preview first. Undo and source control work as usual.",
        pick: "inPlace",
      });
    }
    items.push(
      {
        label: `$(files) Write an upgraded copy to ${displayFolder(suggested, base.fsPath)}`,
        detail:
          "Your files stay as they are. Everything is copied, upgraded files replace their copies, and " +
          "AIRFLOW_UPGRADE_REPORT.md lists what still needs you.",
        description: folderState(suggested) === "earlier-copy" ? "replaces the earlier copy" : undefined,
        pick: "suggested",
      },
      {
        label: "$(folder-opened) Write an upgraded copy to another folder...",
        detail: "Pick an empty folder, or any folder to create the copy inside it.",
        pick: "browse",
      },
    );
    const choice = await vscode.window.showQuickPick(items, {
      title: `dagshift: ${upgradeLabel(upgrade)}: where should the upgraded files go?`,
      placeHolder:
        folder && base.toString() === folder.uri.toString()
          ? `Upgrading the whole ${folder.name} folder`
          : `Upgrading ${vscode.workspace.asRelativePath(base, true)}`,
      ignoreFocusOut: true,
    });
    if (!choice) {
      return undefined;
    }
    if (choice.pick === "inPlace") {
      return { kind: "inPlace" };
    }
    const outDir = choice.pick === "suggested" ? suggested : await askFolder(base.fsPath, suggested);
    return outDir ? { kind: "copy", outDir } : undefined;
  };

  const writeCopy = async (base: vscode.Uri, folder: vscode.WorkspaceFolder | undefined, outDir: string) => {
    const dirty = vscode.workspace.textDocuments.filter(
      (d) => d.isDirty && d.uri.scheme === "file" && (d.uri.fsPath + path.sep).startsWith(base.fsPath + path.sep),
    );
    if (dirty.length) {
      const save = await vscode.window.showWarningMessage(
        `${dirty.length} file${dirty.length === 1 ? " has" : "s have"} unsaved changes. The upgraded copy is made from what is on disk.`,
        { modal: true },
        "Save All and Continue",
        "Continue Without Saving",
      );
      if (!save) {
        return;
      }
      if (save === "Save All and Continue") {
        await Promise.all(dirty.map((d) => d.save()));
      }
    }
    let replace = false;
    const state = folderState(outDir);
    if (state === "earlier-copy") {
      const ok = await vscode.window.showWarningMessage(
        `Replace the earlier upgraded copy in ${displayFolder(outDir, base.fsPath)}?`,
        { modal: true, detail: "It was written by dagshift. Your original files are not affected." },
        "Replace",
      );
      if (ok !== "Replace") {
        return;
      }
      replace = true;
    } else if (state === "other") {
      const again = await vscode.window.showErrorMessage(
        `dagshift: ${outDir} already has files that dagshift did not write, so it will not use it.`,
        "Choose Another Folder...",
      );
      if (again) {
        const other = await askFolder(base.fsPath, outDir);
        if (other) {
          await writeCopy(base, folder, other);
        }
      }
      return;
    }

    const upgrade = model.upgradeFor(folder);
    const options = engineOptions(base);
    const isRoot = folder !== undefined && base.toString() === folder.uri.toString();
    if (!options.source && upgrade?.source && !isRoot) {
      // a sub-folder may not contain the pins the workspace was detected from: keep the status-bar version
      options.source = upgrade.source;
    }
    const res = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `dagshift: writing the upgraded copy (${upgradeLabel(upgrade)})` },
      async () => (await engine.get()).writeCopy({
        root: folder?.uri.fsPath ?? base.fsPath,
        path: base.fsPath,
        outDir,
        options,
        replace,
      }),
    );
    if (!res.ok) {
      void vscode.window.showErrorMessage(`dagshift: ${res.message}`);
      return;
    }
    const copy: UpgradedCopy = {
      base: base.fsPath,
      outDir: res.outDir,
      source: res.source,
      target: res.target,
      changed: res.changed,
      report: res.report,
    };
    setCopy(copy);
    log.info(`Upgraded copy: ${res.changed.length} files changed in ${res.outDir}`);
    const where = displayFolder(res.outDir, base.fsPath);
    const s = res.summary;
    const actions = ["Compare Changes", "Open Folder"];
    if (res.report) {
      actions.push("Open Report");
    }
    const pick = await vscode.window.showInformationMessage(
      `dagshift: upgraded copy written to ${where} (Airflow ${res.source ?? "?"} → ${res.target}). ` +
        `${s.files_changed} files changed with ${s.fixed} fixes; ${s.manual} items need you. Your original files are untouched.`,
      ...actions,
    );
    await afterCopy(pick, copy);
  };

  const afterCopy = async (pick: string | undefined, copy: UpgradedCopy) => {
    if (pick === "Compare Changes") {
      await compareCopy(copy);
    } else if (pick === "Open Folder") {
      const uri = vscode.Uri.file(copy.outDir);
      if (vscode.workspace.getWorkspaceFolder(uri)) {
        await vscode.commands.executeCommand("revealInExplorer", uri);
      } else {
        await vscode.commands.executeCommand("vscode.openFolder", uri, { forceNewWindow: true });
      }
    } else if (pick === "Open Report" && copy.report) {
      await vscode.commands.executeCommand("markdown.showPreview", vscode.Uri.file(copy.report));
    }
  };

  const compareCopy = async (copy = lastCopy()) => {
    if (!copy) {
      void vscode.window.showInformationMessage("dagshift: no upgraded copy yet. Run Upgrade Workspace and choose to write a copy.");
      return;
    }
    const files = copy.changed.filter((c) => fs.existsSync(c.original) && fs.existsSync(c.upgraded));
    if (!files.length) {
      void vscode.window.showWarningMessage(`dagshift: the upgraded copy in ${copy.outDir} is gone or has no changed files.`);
      return;
    }
    const title = `Airflow ${copy.source ?? "?"} → ${copy.target}: originals ↔ upgraded copy`;
    try {
      await vscode.commands.executeCommand(
        "vscode.changes",
        title,
        files.map((c) => [vscode.Uri.file(c.upgraded), vscode.Uri.file(c.original), vscode.Uri.file(c.upgraded)]),
      );
    } catch {
      // editors without the multi-file diff: one file at a time
      const pick = await vscode.window.showQuickPick(
        files.map((c) => ({ label: c.relPath, description: `${c.fixes} fixes${c.manual ? ` · ${c.manual} need you` : ""}`, c })),
        { title },
      );
      if (pick) {
        await vscode.commands.executeCommand(
          "vscode.diff",
          vscode.Uri.file(pick.c.original),
          vscode.Uri.file(pick.c.upgraded),
          `${path.basename(pick.c.relPath)} (original ↔ Airflow ${copy.target} copy)`,
        );
      }
    }
  };

  const upgrade = async (within: vscode.Uri | undefined, forceCopy: boolean) => {
    const target = await upgradeBase(within);
    if (!target) {
      return;
    }
    const dest = await chooseDestination(target.base, target.folder, forceCopy);
    if (dest?.kind === "inPlace") {
      await migrateMany(within);
    } else if (dest?.kind === "copy") {
      await writeCopy(target.base, target.folder, dest.outDir);
    }
  };

  register("dagshift.migrateWorkspace", () => upgrade(undefined, false));
  register("dagshift.migrateFolder", (uri: vscode.Uri) => upgrade(uri, false));
  register("dagshift.upgradeToFolder", (uri?: vscode.Uri) => upgrade(uri instanceof vscode.Uri ? uri : undefined, true));
  register("dagshift.compareUpgradedCopy", () => compareCopy());

  // ---- scanning / engine -----------------------------------------------------------
  register("dagshift.scanWorkspace", () => controller.rescanAll(true));
  register("dagshift.restartEngine", async () => {
    await engine.restart(false);
    await controller.rescanAll();
  });
  register("dagshift.setupEngine", async () => {
    await engine.restart(true);
    await controller.rescanAll();
  });
  register("dagshift.showLog", () => log.show());

  // ---- target ----------------------------------------------------------------------
  register("dagshift.selectVersions", async (arg?: vscode.WorkspaceFolder) => {
    const folder = arg ?? (await pickFolder(model));
    if (!folder) {
      return;
    }
    const upgrade = model.upgradeFor(folder);
    const items: (vscode.QuickPickItem & { run: string })[] = [
      {
        label: `$(history) Current version: ${upgrade?.source ? `Airflow ${sourceLabel(upgrade)}` : "unknown"}`,
        detail: detectionSummary(upgrade) || "Pick the version this project runs today",
        run: "dagshift.selectSource",
      },
      {
        label: `$(arrow-up) Target version: Airflow ${upgrade?.target ?? "?"}`,
        detail: upgrade ? `Rules verified on a real Airflow ${upgrade.verifiedAgainst} install` : undefined,
        run: "dagshift.selectTarget",
      },
    ];
    const pick = await vscode.window.showQuickPick(items, {
      title: `dagshift: ${upgradeLabel(upgrade)}${folder && (vscode.workspace.workspaceFolders?.length ?? 0) > 1 ? ` (${folder.name})` : ""}`,
      placeHolder: "Change the current or the target Airflow version",
    });
    if (pick) {
      await vscode.commands.executeCommand(pick.run, folder);
    }
  });

  register("dagshift.selectSource", async (arg?: vscode.WorkspaceFolder) => {
    const folder = arg ?? (await pickFolder(model));
    if (!folder) {
      return;
    }
    const client = await engine.get();
    const upgrade = model.upgradeFor(folder);
    const cfg = vscode.workspace.getConfiguration("dagshift", folder.uri);
    const configured = explicitSource(folder.uri);
    type Item = vscode.QuickPickItem & { action?: "auto" | "browse" | "version"; version?: string };
    const det = upgrade?.detection ?? (await client.detect(folder.uri.fsPath, sourcePaths(folder.uri)));
    const items: Item[] = [
      {
        label: "$(search) Detect automatically",
        description: det.version ? `finds Airflow ${det.version} (${det.confidence})` : "nothing found",
        detail: det.note || undefined,
        picked: !configured,
        action: "auto",
      },
      {
        label: "$(folder-opened) Detect from another folder or file...",
        detail: "For example the Terraform repository that defines your Cloud Composer or MWAA environment",
        action: "browse",
      },
    ];
    const pinned = new Map<string, string>();
    for (const e of det.evidence) {
      if (e.kind !== "code" && !pinned.has(minorOf(e.version))) {
        pinned.set(minorOf(e.version), `${e.version} in ${e.file}:${e.line} (${e.kind})`);
      }
    }
    if (pinned.size) {
      items.push({
        label: sourcePaths(folder.uri).length ? "Found in this workspace and linked paths" : "Found in this workspace",
        kind: vscode.QuickPickItemKind.Separator,
      });
      for (const [minor, where] of pinned) {
        items.push({ label: `Airflow ${minor}`, description: where, action: "version", version: minor });
      }
    }
    items.push({ label: "Choose a version", kind: vscode.QuickPickItemKind.Separator });
    for (const v of [...(client.info?.sources ?? [])].reverse()) {
      items.push({
        label: `Airflow ${v}`,
        description: v === configured ? "selected" : undefined,
        action: "version",
        version: v,
      });
    }
    const pick = await vscode.window.showQuickPick(items, {
      title: "Which Airflow version does this project run today?",
      placeHolder: "Auto-detect, point at your infrastructure repository, or pick a version",
      matchOnDescription: true,
    });
    if (!pick?.action) {
      return;
    }
    const scope = (vscode.workspace.workspaceFolders?.length ?? 0) > 1
      ? vscode.ConfigurationTarget.WorkspaceFolder
      : vscode.ConfigurationTarget.Workspace;
    if (pick.action === "auto") {
      await cfg.update("sourceVersion", "auto", scope);
    } else if (pick.action === "version") {
      await cfg.update("sourceVersion", pick.version, scope);
    } else {
      const chosen = await vscode.window.showOpenDialog({
        canSelectFiles: true,
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: "Detect From Here",
        title: "Folder or file that defines your Airflow environment (Terraform, Helm values, Dockerfile, requirements)",
      });
      if (!chosen?.[0]) {
        return;
      }
      const found = await client.detect(chosen[0].fsPath, []);
      if (!found.version || found.confidence !== "pinned") {
        const again = await vscode.window.showWarningMessage(
          `dagshift found no pinned Airflow version in ${chosen[0].fsPath}.`,
          "Pick a Version...",
        );
        if (again) {
          await vscode.commands.executeCommand("dagshift.selectSource", folder);
        }
        return;
      }
      const ev = found.evidence[0];
      const current = cfg.inspect<string[]>("sourcePaths")?.workspaceValue ?? [];
      await cfg.update("sourcePaths", [...new Set([...current, chosen[0].fsPath])], scope);
      await cfg.update("sourceVersion", "auto", scope);
      void vscode.window.showInformationMessage(
        `dagshift: current version Airflow ${found.version}, from ${ev.file}:${ev.line} in ${path.basename(chosen[0].fsPath)}.`,
      );
    }
  });

  register("dagshift.selectTarget", async (arg?: vscode.WorkspaceFolder) => {
    const folder = arg ?? (await pickFolder(model));
    const client = await engine.get();
    const upgrade = model.upgradeFor(folder);
    const source = upgrade?.source;
    const targets = (client.info?.targets ?? []).filter((t) => !source || cmpVersion(t.target, source) >= 0);
    const latest = targets[targets.length - 1]?.target;
    const items: (vscode.QuickPickItem & { target?: string })[] = [...targets].reverse().map((t) => ({
      label: `Airflow ${t.target}`,
      description: [
        t.target === upgrade?.target ? "selected" : "",
        t.target === latest ? "latest" : "",
        source && cmpVersion(t.target, source) === 0 ? "same as current: deprecation fixes only" : "",
      ].filter(Boolean).join(" · ") || undefined,
      detail: `Rules verified on a real Airflow ${t.verifiedAgainst} install`,
      target: t.target,
    }));
    items.push({ label: "", kind: vscode.QuickPickItemKind.Separator });
    items.push({ label: "Use the project's setting", detail: "Clear the VS Code setting; dagshift.toml / [tool.dagshift] decides" });
    const pick = await vscode.window.showQuickPick(items, {
      title: source ? `Upgrade from Airflow ${sourceLabel(upgrade)} to...` : "Upgrade to which Airflow version?",
      placeHolder: "Target Airflow version",
    });
    if (!pick || pick.kind === vscode.QuickPickItemKind.Separator) {
      return;
    }
    const scope = (vscode.workspace.workspaceFolders?.length ?? 0) > 1
      ? vscode.ConfigurationTarget.WorkspaceFolder
      : vscode.ConfigurationTarget.Workspace;
    await vscode.workspace.getConfiguration("dagshift", folder?.uri).update("target", pick.target, scope);
  });

  // ---- report ----------------------------------------------------------------------
  register("dagshift.showReport", async () => {
    const folder = await pickFolder(model);
    if (!folder) {
      return;
    }
    const client = await engine.get();
    const res = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Window, title: "dagshift: building report" },
      () => client.report(folder.uri.fsPath, "md", engineOptions(folder.uri)),
    );
    const file = path.join(storageDir(ctx), `migration-report-${folder.name}.md`);
    fs.writeFileSync(file, res.text, "utf8");
    await vscode.commands.executeCommand("markdown.showPreview", vscode.Uri.file(file));
  });

  register("dagshift.exportReport", async () => {
    const folder = await pickFolder(model);
    if (!folder) {
      return;
    }
    const dest = await vscode.window.showSaveDialog({
      defaultUri: vscode.Uri.joinPath(folder.uri, "MIGRATION_REPORT.md"),
      filters: { Markdown: ["md"], JSON: ["json"], SARIF: ["sarif"], Text: ["txt"] },
      saveLabel: "Export Report",
    });
    if (!dest) {
      return;
    }
    const ext = path.extname(dest.fsPath).toLowerCase();
    const format = ext === ".json" ? "json" : ext === ".sarif" ? "sarif" : ext === ".txt" ? "text" : "md";
    const client = await engine.get();
    const res = await client.report(folder.uri.fsPath, format, engineOptions(folder.uri));
    await vscode.workspace.fs.writeFile(dest, Buffer.from(res.text, "utf8"));
    void vscode.window.showInformationMessage(`dagshift: report written to ${vscode.workspace.asRelativePath(dest)}`);
  });

  // ---- recipes and rules -----------------------------------------------------------
  register("dagshift.showRecipe", async (arg: Finding | { finding?: Finding }) => {
    const f: Finding | undefined = arg && "rule_id" in arg ? (arg as Finding) : (arg as { finding?: Finding })?.finding;
    if (!f) {
      return;
    }
    const where = f.absPath ? vscode.workspace.asRelativePath(f.absPath) : f.path;
    const md =
      `# ${f.rule_id}: how to fix\n\n` +
      `\`${where}:${f.line}\` · ${severityLabel(f.severity)}\n\n${f.message}\n\n` +
      (f.recipe ? `## What to do\n\n${recipeMarkdown(f.recipe)}\n` : "");
    const file = path.join(storageDir(ctx), "recipes", `${f.rule_id}-${path.basename(f.path)}-${f.line}.md`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, md, "utf8");
    await vscode.commands.executeCommand("markdown.showPreviewToSide", vscode.Uri.file(file));
  });

  register("dagshift.ignoreRule", async (arg: string | { finding?: Finding }) => {
    const id = typeof arg === "string" ? arg : arg?.finding?.rule_id;
    if (!id) {
      return;
    }
    const cfg = vscode.workspace.getConfiguration("dagshift");
    const current = cfg.inspect<string[]>("ignore")?.workspaceValue ?? [];
    if (current.includes(id)) {
      return;
    }
    await cfg.update("ignore", [...current, id], vscode.ConfigurationTarget.Workspace);
    const undo = await vscode.window.showInformationMessage(`dagshift: ignoring ${id} in this workspace.`, "Undo");
    if (undo) {
      await cfg.update("ignore", current, vscode.ConfigurationTarget.Workspace);
    }
  });

  // ---- validate on a real install of the target -------------------------------------------------
  register("dagshift.validate", async () => {
    if (!vscode.workspace.isTrusted) {
      void vscode.window.showWarningMessage(
        "dagshift: validation runs your DAG files, so it is disabled in Restricted Mode. Trust the workspace to use it.",
      );
      return;
    }
    const folder = await pickFolder(model);
    if (!folder) {
      return;
    }
    const cfg = vscode.workspace.getConfiguration("dagshift", folder.uri);
    let airflowPython = cfg.get<string>("validate.airflowPython")?.trim();
    if (!airflowPython) {
      const choice = await vscode.window.showInformationMessage(
        "dagshift validates DAGs by loading them with a real install of the target Airflow. Select the Python " +
          "interpreter of an environment that has it installed (for example a venv with apache-airflow==" +
          `${model.upgradeFor(folder)?.target ?? "3.3"}.*).`,
        { modal: true },
        "Select Interpreter...",
      );
      if (choice !== "Select Interpreter...") {
        return;
      }
      const picked = await vscode.window.showOpenDialog({
        canSelectFiles: true,
        canSelectFolders: false,
        canSelectMany: false,
        openLabel: "Use This Python",
        title: "Python interpreter with the target Airflow version",
      });
      if (!picked?.[0]) {
        return;
      }
      airflowPython = picked[0].fsPath;
      await cfg.update("validate.airflowPython", airflowPython, vscode.ConfigurationTarget.Workspace);
    }
    let dagFolderRel = cfg.get<string>("validate.dagFolder")?.trim();
    if (!dagFolderRel) {
      const candidates = await findDagFolders(folder);
      const picks: vscode.QuickPickItem[] = candidates.map((c) => ({ label: c }));
      picks.push({ label: "Browse..." });
      const pick =
        candidates.length === 1
          ? picks[0]
          : await vscode.window.showQuickPick(picks, { placeHolder: "Which folder holds the DAG files?" });
      if (!pick) {
        return;
      }
      if (pick.label === "Browse...") {
        const chosen = await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false, defaultUri: folder.uri });
        if (!chosen?.[0]) {
          return;
        }
        dagFolderRel = path.relative(folder.uri.fsPath, chosen[0].fsPath) || ".";
      } else {
        dagFolderRel = pick.label;
      }
      await cfg.update("validate.dagFolder", dagFolderRel, vscode.ConfigurationTarget.WorkspaceFolder);
    }
    const dagFolder = path.resolve(folder.uri.fsPath, dagFolderRel);
    const renderCmd = cfg.get<string>("validate.renderCommand")?.trim() || undefined;
    const pythonpath = (cfg.get<string[]>("validate.pythonPath") ?? []).map((p) => path.resolve(folder.uri.fsPath, p));
    if (!pythonpath.length) {
      pythonpath.push(folder.uri.fsPath, path.dirname(dagFolder));
    }
    const client = await engine.get();
    const res: ValidateResult = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `dagshift: loading ${dagFolderRel} with the target Airflow`, cancellable: false },
      () => client.validate({ dagFolder, airflowPython: airflowPython!, renderCmd, pythonpath }),
    );
    log.info(`Validation of ${dagFolder}:\n${res.text}`);
    renderValidation(validateDiagnostics, dagFolder, res);
    const n = Object.keys(res.dags).length;
    const errs = Object.keys(res.importErrors).length;
    const msg = res.fatal
      ? `dagshift: validation could not run: ${res.fatal}`
      : `Airflow ${res.airflowVersion}: ${n} DAG${n === 1 ? "" : "s"} loaded, ${errs} import error${errs === 1 ? "" : "s"}` +
        (res.warnings.length ? `, ${res.warnings.length} deprecation warning${res.warnings.length === 1 ? "" : "s"}` : "") +
        ".";
    const show = res.ok && !res.warnings.length ? vscode.window.showInformationMessage : vscode.window.showWarningMessage;
    const choice = await show(msg, "Show Details");
    if (choice) {
      log.show(true);
    }
  });
}

async function findDagFolders(folder: vscode.WorkspaceFolder): Promise<string[]> {
  const files = await vscode.workspace.findFiles(
    new vscode.RelativePattern(folder, "**/dags/**/*.py"),
    "**/{node_modules,.git,.venv,venv,site-packages}/**",
    2000,
  );
  const dirs = new Set<string>();
  for (const f of files) {
    const rel = path.relative(folder.uri.fsPath, f.fsPath);
    const parts = rel.split(path.sep);
    const i = parts.lastIndexOf("dags");
    if (i >= 0) {
      dirs.add(parts.slice(0, i + 1).join("/"));
    }
  }
  return [...dirs].sort((a, b) => a.length - b.length);
}

function renderValidation(collection: vscode.DiagnosticCollection, dagFolder: string, res: ValidateResult): void {
  collection.clear();
  const byFile = new Map<string, vscode.Diagnostic[]>();
  const add = (file: string, d: vscode.Diagnostic) => byFile.set(file, [...(byFile.get(file) ?? []), d]);
  for (const [rel, trace] of Object.entries(res.importErrors)) {
    const abs = path.resolve(dagFolder, rel);
    let line = 0;
    for (const m of trace.matchAll(/File "([^"]+)", line (\d+)/g)) {
      if (path.resolve(m[1]) === abs || m[1].endsWith(rel)) {
        line = Number(m[2]) - 1;
      }
    }
    const last = trace.trim().split("\n").pop() ?? trace;
    const d = new vscode.Diagnostic(new vscode.Range(line, 0, line, 10_000), `Airflow ${res.airflowVersion} import error: ${last}`,
      vscode.DiagnosticSeverity.Error);
    d.source = "dagshift validate";
    add(abs, d);
  }
  for (const w of res.warnings) {
    const abs = path.resolve(dagFolder, w.file);
    const line = Math.max(0, w.line - 1);
    const d = new vscode.Diagnostic(new vscode.Range(line, 0, line, 10_000), `Airflow ${res.airflowVersion}: ${w.message}`,
      vscode.DiagnosticSeverity.Warning);
    d.source = "dagshift validate";
    add(abs, d);
  }
  for (const [file, diags] of byFile) {
    collection.set(vscode.Uri.file(file), diags);
  }
}

