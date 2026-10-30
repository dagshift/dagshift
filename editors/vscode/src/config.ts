/** VS Code settings -> engine options. Only settings the user actually set are sent, so the
 * project's dagshift.toml / [tool.dagshift] stays in charge of everything else. */
import * as path from "node:path";
import * as vscode from "vscode";
import type { EngineOptions } from "./engineClient";

const OPTION_SETTINGS = [
  "target",
  "startDate",
  "modernize",
  "preserveScheduleDefault",
  "pinCatchup",
  "keepCronDataIntervals",
  "renameConfigKeys",
  "migrateStrings",
  "select",
  "ignore",
  "exclude",
] as const;

export function engineOptions(scope?: vscode.Uri): EngineOptions {
  const cfg = vscode.workspace.getConfiguration("dagshift", scope);
  const out: EngineOptions = {};
  for (const key of OPTION_SETTINGS) {
    const i = cfg.inspect(key);
    const value = i?.workspaceFolderValue ?? i?.workspaceValue ?? i?.globalValue;
    if (value === undefined || value === "") {
      continue;
    }
    out[key] = value;
  }
  const source = explicitSource(scope);
  if (source) {
    out.source = source;
  }
  const paths = sourcePaths(scope);
  if (paths.length) {
    out.sourcePaths = paths;
  }
  return out;
}

/** The current version the user picked, or undefined for auto-detection. */
export function explicitSource(scope?: vscode.Uri): string | undefined {
  const v = vscode.workspace.getConfiguration("dagshift", scope).get<string>("sourceVersion")?.trim();
  return v && v !== "auto" ? v : undefined;
}

/** dagshift.sourcePaths resolved against the workspace folder. */
export function sourcePaths(scope?: vscode.Uri): string[] {
  const folder = scope ? vscode.workspace.getWorkspaceFolder(scope) : vscode.workspace.workspaceFolders?.[0];
  const raw = vscode.workspace.getConfiguration("dagshift", scope).get<string[]>("sourcePaths") ?? [];
  return raw
    .filter((p) => p.trim())
    .map((p) => (path.isAbsolute(p) || !folder ? p : path.join(folder.uri.fsPath, p)));
}

/** Settings that change engine results: a change to any of them triggers a rescan. */
export function affectsResults(e: vscode.ConfigurationChangeEvent): boolean {
  return (
    OPTION_SETTINGS.some((k) => e.affectsConfiguration(`dagshift.${k}`)) ||
    e.affectsConfiguration("dagshift.enable") ||
    e.affectsConfiguration("dagshift.sourceVersion") ||
    e.affectsConfiguration("dagshift.sourcePaths")
  );
}

export function setting<T>(key: string, scope?: vscode.Uri): T | undefined {
  return vscode.workspace.getConfiguration("dagshift", scope).get<T>(key);
}
