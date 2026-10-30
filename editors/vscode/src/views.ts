/** Everything that renders the model: diagnostics, hovers, quick fixes, CodeLens, tree, status bar. */
import * as path from "node:path";
import * as vscode from "vscode";
import type { Finding } from "./engineClient";
import { detectionSummary, upgradeLabel, type FileEntry, type FindingsModel } from "./model";
import {
  RULES_URL,
  commandLink,
  diagnosticMessage,
  diagnosticSeverity,
  lineRange,
  recipeMarkdown,
  severityLabel,
  themeIcon,
} from "./presentation";
import { setting } from "./config";

export const DOCUMENT_SELECTOR: vscode.DocumentSelector = [
  { scheme: "file", language: "python" },
  { scheme: "file", language: "yaml" },
  { scheme: "file", language: "json" },
  { scheme: "file", language: "jsonc" },
  { scheme: "file", language: "jinja" },
  { scheme: "file", language: "jinja-python" },
  { scheme: "file", language: "pip-requirements" },
  { scheme: "file", language: "dockerfile" },
  { scheme: "file", language: "terraform" },
  { scheme: "file", language: "plaintext" },
  { scheme: "file", language: "ignore" },
];

// ---------------------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------------------
export class Diagnostics implements vscode.Disposable {
  private readonly collection = vscode.languages.createDiagnosticCollection("dagshift");
  private rendered = new Set<string>();

  constructor(private readonly model: FindingsModel) {}

  render(): void {
    const showInfo = setting<boolean>("showInfo") ?? true;
    const next = new Set<string>();
    for (const uri of this.model.allUris()) {
      const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());
      const diags: vscode.Diagnostic[] = [];
      for (const f of this.model.findingsFor(uri)) {
        if (f.severity === "info" && !showInfo) {
          continue;
        }
        const d = new vscode.Diagnostic(lineRange(f, doc), diagnosticMessage(f), diagnosticSeverity(f));
        d.source = "dagshift";
        d.code = { value: f.rule_id, target: vscode.Uri.parse(`${RULES_URL}#${f.rule_id.toLowerCase()}`) };
        diags.push(d);
      }
      const err = this.model.get(uri)?.report.error;
      if (err) {
        const d = new vscode.Diagnostic(new vscode.Range(0, 0, 0, 1), `dagshift could not analyse this file: ${err}`,
          vscode.DiagnosticSeverity.Information);
        d.source = "dagshift";
        diags.push(d);
      }
      this.collection.set(uri, diags);
      next.add(uri.toString());
    }
    for (const key of this.rendered) {
      if (!next.has(key)) {
        this.collection.delete(vscode.Uri.parse(key));
      }
    }
    this.rendered = next;
  }

  dispose(): void {
    this.collection.dispose();
  }
}

function findingsOnLine(model: FindingsModel, uri: vscode.Uri, line: number): Finding[] {
  return model.findingsFor(uri).filter((f) => f.line - 1 === line);
}

// ---------------------------------------------------------------------------------------
// Hover: how to fix, plus Preview / Apply links
// ---------------------------------------------------------------------------------------
export class RecipeHover implements vscode.HoverProvider {
  constructor(private readonly model: FindingsModel) {}

  provideHover(doc: vscode.TextDocument, pos: vscode.Position): vscode.Hover | undefined {
    const findings = findingsOnLine(this.model, doc.uri, pos.line);
    if (!findings.length) {
      return undefined;
    }
    const entry = this.model.get(doc.uri);
    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = { enabledCommands: ["dagshift.previewFile", "dagshift.applyFile", "dagshift.ignoreRule"] };
    md.supportThemeIcons = true;
    for (const f of findings) {
      md.appendMarkdown(`**dagshift ${f.rule_id}** · ${severityLabel(f.severity)}\n\n`);
      if (f.recipe) {
        md.appendMarkdown(`**How to fix**\n\n${recipeMarkdown(f.recipe)}\n\n`);
      }
    }
    const links: string[] = [];
    if (entry?.report.changed) {
      links.push(`[$(diff) Preview changes](${commandLink("dagshift.previewFile", [doc.uri.toString()])})`);
      links.push(`[$(check) Apply fixes in this file](${commandLink("dagshift.applyFile", [doc.uri.toString()])})`);
    }
    const ids = [...new Set(findings.map((f) => f.rule_id))];
    for (const id of ids) {
      links.push(`[Ignore ${id}](${commandLink("dagshift.ignoreRule", [id])})`);
    }
    md.appendMarkdown(links.join(" · "));
    return new vscode.Hover(md);
  }
}

// ---------------------------------------------------------------------------------------
// Quick fixes and source.fixAll.dagshift
// ---------------------------------------------------------------------------------------
export const FIX_ALL_KIND = vscode.CodeActionKind.SourceFixAll.append("dagshift");

export class QuickFixes implements vscode.CodeActionProvider {
  static readonly metadata: vscode.CodeActionProviderMetadata = {
    providedCodeActionKinds: [vscode.CodeActionKind.QuickFix, FIX_ALL_KIND],
  };

  constructor(
    private readonly model: FindingsModel,
    private readonly computeFix: (doc: vscode.TextDocument) => Promise<string | undefined>,
  ) {}

  async provideCodeActions(
    doc: vscode.TextDocument,
    _range: vscode.Range,
    context: vscode.CodeActionContext,
  ): Promise<vscode.CodeAction[]> {
    const entry = this.model.get(doc.uri);
    if (!entry) {
      return [];
    }
    if (context.only && FIX_ALL_KIND.intersects(context.only)) {
      if (!entry.report.changed) {
        return [];
      }
      const migrated = await this.computeFix(doc);
      if (migrated === undefined) {
        return [];
      }
      const action = new vscode.CodeAction("dagshift: Apply all upgrade fixes", FIX_ALL_KIND);
      action.edit = new vscode.WorkspaceEdit();
      action.edit.replace(doc.uri, fullRange(doc), migrated);
      return [action];
    }
    const ours = context.diagnostics.filter((d) => d.source === "dagshift");
    if (!ours.length) {
      return [];
    }
    const actions: vscode.CodeAction[] = [];
    if (entry.report.changed) {
      const apply = new vscode.CodeAction("dagshift: Apply all upgrade fixes in this file", vscode.CodeActionKind.QuickFix);
      apply.command = { command: "dagshift.applyFile", title: "Apply", arguments: [doc.uri] };
      apply.diagnostics = ours.filter((d) => d.severity !== vscode.DiagnosticSeverity.Error);
      apply.isPreferred = true;
      actions.push(apply);
      const preview = new vscode.CodeAction("dagshift: Preview upgrade changes", vscode.CodeActionKind.QuickFix);
      preview.command = { command: "dagshift.previewFile", title: "Preview", arguments: [doc.uri] };
      actions.push(preview);
    }
    const seen = new Set<string>();
    for (const d of ours) {
      const id = typeof d.code === "object" ? String(d.code.value) : String(d.code);
      if (seen.has(id)) {
        continue;
      }
      seen.add(id);
      const f = findingsOnLine(this.model, doc.uri, d.range.start.line).find((x) => x.rule_id === id);
      if (f?.recipe) {
        const how = new vscode.CodeAction(`dagshift: How to fix ${id}`, vscode.CodeActionKind.QuickFix);
        how.command = { command: "dagshift.showRecipe", title: "How to fix", arguments: [f] };
        how.diagnostics = [d];
        actions.push(how);
      }
      const ignore = new vscode.CodeAction(`dagshift: Ignore ${id} in this workspace`, vscode.CodeActionKind.QuickFix);
      ignore.command = { command: "dagshift.ignoreRule", title: "Ignore", arguments: [id] };
      actions.push(ignore);
    }
    return actions;
  }
}

export function fullRange(doc: vscode.TextDocument): vscode.Range {
  return new vscode.Range(0, 0, doc.lineCount, 0);
}

// ---------------------------------------------------------------------------------------
// CodeLens at the top of migratable files
// ---------------------------------------------------------------------------------------
export class MigrateLens implements vscode.CodeLensProvider {
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChange.event;

  constructor(private readonly model: FindingsModel) {
    model.onDidChange(() => this._onDidChange.fire());
  }

  provideCodeLenses(doc: vscode.TextDocument): vscode.CodeLens[] {
    if (!(setting<boolean>("codeLens", doc.uri) ?? true)) {
      return [];
    }
    const entry = this.model.get(doc.uri);
    if (!entry || (!entry.report.changed && !entry.report.findings.some((f) => f.severity === "manual"))) {
      return [];
    }
    const top = new vscode.Range(0, 0, 0, 0);
    const fixes = entry.report.findings.filter((f) => f.severity === "fixed").length;
    const manual = entry.report.findings.filter((f) => f.severity === "manual").length;
    const lenses: vscode.CodeLens[] = [];
    if (entry.report.changed) {
      lenses.push(
        new vscode.CodeLens(top, {
          title: `$(arrow-up) ${upgradeLabel(this.model.upgradeFor(vscode.workspace.getWorkspaceFolder(doc.uri)))}: ` +
            `${fixes} auto-fix${fixes === 1 ? "" : "es"} ready · Preview`,
          command: "dagshift.previewFile",
          arguments: [doc.uri],
        }),
        new vscode.CodeLens(top, { title: "Apply", command: "dagshift.applyFile", arguments: [doc.uri] }),
      );
    }
    if (manual) {
      lenses.push(
        new vscode.CodeLens(top, {
          title: `$(error) ${manual} need${manual === 1 ? "s" : ""} you`,
          command: "workbench.actions.view.problems",
        }),
      );
    }
    return lenses;
  }
}

// ---------------------------------------------------------------------------------------
// Tree view
// ---------------------------------------------------------------------------------------
type Node =
  | { kind: "file"; entry: FileEntry }
  | { kind: "finding"; uri: vscode.Uri | undefined; finding: Finding }
  | { kind: "project"; items: { uri: vscode.Uri | undefined; finding: Finding }[] };

export class FindingsTree implements vscode.TreeDataProvider<Node> {
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  constructor(private readonly model: FindingsModel) {
    model.onDidChange(() => this._onDidChange.fire());
  }

  getChildren(node?: Node): Node[] {
    const showInfo = setting<boolean>("showInfo") ?? true;
    const keep = (f: Finding) => showInfo || f.severity !== "info";
    if (!node) {
      const rank = (e: FileEntry) =>
        e.report.findings.some((f) => f.severity === "manual") ? 0 : e.report.changed ? 1 : 2;
      const files: Node[] = this.model
        .entries()
        .filter((e) => e.report.changed || e.report.error || e.report.findings.some(keep))
        .sort((a, b) => rank(a) - rank(b) || a.uri.path.localeCompare(b.uri.path))
        .map((entry) => ({ kind: "file", entry }));
      const orphans = this.model.orphanProjectFindings().filter((o) => keep(o.finding));
      return orphans.length ? [...files, { kind: "project", items: orphans }] : files;
    }
    if (node.kind === "file") {
      return this.model
        .findingsFor(node.entry.uri)
        .filter(keep)
        .sort((a, b) => a.line - b.line)
        .map((finding) => ({ kind: "finding", uri: node.entry.uri, finding }));
    }
    if (node.kind === "project") {
      return node.items.map((i) => ({ kind: "finding", uri: i.uri, finding: i.finding }));
    }
    return [];
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "file") {
      const { entry } = node;
      const r = entry.report;
      const item = new vscode.TreeItem(entry.uri, vscode.TreeItemCollapsibleState.Collapsed);
      const counts: string[] = [];
      const n = (s: string) => r.findings.filter((f) => f.severity === s).length;
      if (n("fixed")) counts.push(`${n("fixed")} fix${n("fixed") === 1 ? "" : "es"}`);
      if (n("manual")) counts.push(`${n("manual")} need you`);
      if (n("warn")) counts.push(`${n("warn")} warn`);
      const rel = entry.folder ? path.posix.dirname(path.posix.relative(entry.folder.uri.path, entry.uri.path)) : "";
      item.description = [rel && rel !== "." ? rel : "", counts.join(" · ")].filter(Boolean).join("  ");
      item.contextValue = r.changed ? "file-changed" : "file";
      item.tooltip = new vscode.MarkdownString(
        `**${vscode.workspace.asRelativePath(entry.uri)}**\n\n${counts.join(" · ") || "notes only"}` +
          (r.templated ? "\n\nJinja template: values injected at render time are checked after rendering." : "") +
          (r.error ? `\n\n${r.error}` : ""),
      );
      return item;
    }
    if (node.kind === "project") {
      const item = new vscode.TreeItem("Project", vscode.TreeItemCollapsibleState.Expanded);
      item.iconPath = new vscode.ThemeIcon("package");
      item.description = "requirements and environment";
      return item;
    }
    const f = node.finding;
    const item = new vscode.TreeItem(`${f.rule_id}  ${firstSentence(f.message)}`, vscode.TreeItemCollapsibleState.None);
    item.description = `line ${f.line}`;
    item.iconPath = themeIcon(f.severity);
    item.contextValue = `finding-${f.severity}`;
    const tip = new vscode.MarkdownString(`**${f.rule_id}** · ${severityLabel(f.severity)}\n\n${f.message}`);
    if (f.recipe) {
      tip.appendMarkdown(`\n\n**How to fix**\n\n${recipeMarkdown(f.recipe)}`);
    }
    item.tooltip = tip;
    if (node.uri) {
      item.command = {
        command: "vscode.open",
        title: "Open",
        arguments: [node.uri, { selection: new vscode.Range(Math.max(0, f.line - 1), 0, Math.max(0, f.line - 1), 0) }],
      };
    }
    return item;
  }
}

function firstSentence(msg: string): string {
  const cut = msg.search(/(?<=[.:;])\s/);
  const s = cut > 0 ? msg.slice(0, cut) : msg;
  return s.length > 110 ? `${s.slice(0, 107)}...` : s;
}

// ---------------------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------------------
export class StatusBar implements vscode.Disposable {
  private readonly item = vscode.window.createStatusBarItem("dagshift.status", vscode.StatusBarAlignment.Left, 50);
  private state: "idle" | "setup" | "scanning" | "error" = "idle";
  private detected = false;

  constructor(private readonly model: FindingsModel) {
    this.item.name = "dagshift";
    this.item.command = "dagshift.findings.focus";
    model.onDidChange(() => this.update());
  }

  setState(state: "idle" | "setup" | "scanning" | "error"): void {
    this.state = state;
    this.update();
  }

  setDetected(detected: boolean): void {
    this.detected = detected;
    this.update();
  }

  private update(): void {
    if (!this.detected && this.state === "idle") {
      this.item.hide();
      return;
    }
    const t = this.model.totals();
    const upgrade = this.model.upgradeFor(undefined);
    const label = upgradeLabel(upgrade);
    this.item.command = "dagshift.selectVersions";
    if (this.state === "setup") {
      this.item.text = "$(cloud-download) dagshift: setting up";
      this.item.tooltip = "Creating the dagshift engine environment (one time)";
    } else if (this.state === "scanning") {
      this.item.text = `$(sync~spin) ${upgrade ? label : "dagshift"}: scanning`;
      this.item.tooltip = "Scanning the workspace";
    } else if (this.state === "error") {
      this.item.text = "$(warning) dagshift";
      this.item.tooltip = "The dagshift engine is not running. Click for details.";
      this.item.command = "dagshift.showLog";
    } else {
      const parts: string[] = [];
      if (t.fixable + t.manual + t.warn === 0) {
        parts.push("ready");
      } else {
        parts.push(`${t.fixable} fixes`);
        if (t.manual) parts.push(`${t.manual} need you`);
      }
      this.item.text = `$(arrow-up) ${label} · ${parts.join(" · ")}`;
      const md = new vscode.MarkdownString(undefined, true);
      md.isTrusted = { enabledCommands: ["dagshift.selectVersions", "dagshift.findings.focus"] };
      md.appendMarkdown(`**${label}**\n\n`);
      const how = detectionSummary(upgrade);
      if (how) {
        md.appendMarkdown(`${how}\n\n`);
      }
      if (upgrade) {
        md.appendMarkdown(`Rules verified on Airflow ${upgrade.verifiedAgainst}.\n\n`);
      }
      md.appendMarkdown(
        `${t.filesChanged} files can be updated automatically · ${t.fixable} fixes · ${t.manual} need you · ` +
          `${t.warn} behaviour changes · ${t.info} notes\n\n`,
      );
      md.appendMarkdown("[$(versions) Change versions](command:dagshift.selectVersions) · [$(list-tree) Findings](command:dagshift.findings.focus)");
      this.item.tooltip = md;
    }
    this.item.show();
  }

  dispose(): void {
    this.item.dispose();
  }
}
