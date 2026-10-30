import * as vscode from "vscode";
import { MigratedContent, PREVIEW_SCHEME, registerActions } from "./actions";
import { Controller } from "./controller";
import { Engine } from "./engine";
import { FindingsModel, upgradeLabel } from "./model";
import { DOCUMENT_SELECTOR, Diagnostics, FindingsTree, MigrateLens, QuickFixes, RecipeHover, StatusBar } from "./views";
import { engineOptions } from "./config";

export async function activate(ctx: vscode.ExtensionContext): Promise<{ model: FindingsModel; engine: Engine }> {
  const log = vscode.window.createOutputChannel("dagshift", { log: true });
  const model = new FindingsModel();
  const engine = new Engine(ctx, log);
  const diagnostics = new Diagnostics(model);
  const status = new StatusBar(model);
  const content = new MigratedContent();
  const controller = new Controller(ctx, engine, model, diagnostics, status, log);

  const tree = vscode.window.createTreeView("dagshift.findings", {
    treeDataProvider: new FindingsTree(model),
    showCollapseAll: true,
  });
  model.onDidChange(() => {
    const t = model.totals();
    tree.badge = t.manual ? { value: t.manual, tooltip: `${t.manual} items need your decision` } : undefined;
    const upgrade = model.upgradeFor(undefined);
    tree.message = upgrade
      ? `${upgradeLabel(upgrade)} · ${t.filesChanged} files to update · ${t.fixable} fixes · ${t.manual} need you`
      : undefined;
    tree.description = upgrade ? upgradeLabel(upgrade) : undefined;
  });

  const computeFix = async (doc: vscode.TextDocument): Promise<string | undefined> => {
    const client = await engine.get();
    const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
    const res = await client.migrateDocument(doc.uri.fsPath, doc.getText(), folder?.uri.fsPath, engineOptions(doc.uri));
    return res.changed && res.migrated ? res.migrated : undefined;
  };

  ctx.subscriptions.push(
    log,
    model,
    engine,
    diagnostics,
    status,
    controller,
    tree,
    vscode.workspace.registerTextDocumentContentProvider(PREVIEW_SCHEME, content),
    vscode.languages.registerHoverProvider(DOCUMENT_SELECTOR, new RecipeHover(model)),
    vscode.languages.registerCodeActionsProvider(DOCUMENT_SELECTOR, new QuickFixes(model, computeFix), QuickFixes.metadata),
    vscode.languages.registerCodeLensProvider(DOCUMENT_SELECTOR, new MigrateLens(model)),
  );
  registerActions(ctx, engine, model, controller, content, log);

  log.info(`dagshift extension ${ctx.extension.packageJSON.version} activated`);
  void controller.start();
  return { model, engine };
}

export function deactivate(): void {
  // disposables registered on the context stop the engine
}
