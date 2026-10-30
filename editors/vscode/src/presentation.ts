/** Shared wording, icons and ranges for findings. */
import * as vscode from "vscode";
import type { Finding, Severity } from "./engineClient";

export const RULES_URL = "https://dagshift.github.io/dagshift/rules/";

export function severityLabel(s: Severity): string {
  return { fixed: "auto-fix", manual: "needs you", warn: "behaviour change", info: "note" }[s];
}

export function diagnosticSeverity(f: Finding): vscode.DiagnosticSeverity {
  switch (f.severity) {
    case "manual":
      return vscode.DiagnosticSeverity.Error;
    case "warn":
      return vscode.DiagnosticSeverity.Warning;
    case "fixed":
      // DS102 = still works on the target, only modernized
      return f.rule_id === "DS102" ? vscode.DiagnosticSeverity.Information : vscode.DiagnosticSeverity.Warning;
    default:
      return vscode.DiagnosticSeverity.Information;
  }
}

export function themeIcon(s: Severity): vscode.ThemeIcon {
  switch (s) {
    case "manual":
      return new vscode.ThemeIcon("error", new vscode.ThemeColor("problemsErrorIcon.foreground"));
    case "warn":
      return new vscode.ThemeIcon("warning", new vscode.ThemeColor("problemsWarningIcon.foreground"));
    case "fixed":
      return new vscode.ThemeIcon("wand", new vscode.ThemeColor("charts.blue"));
    default:
      return new vscode.ThemeIcon("info", new vscode.ThemeColor("problemsInfoIcon.foreground"));
  }
}

export function diagnosticMessage(f: Finding): string {
  return f.severity === "fixed" ? `Auto-fix available: ${f.message}` : f.message;
}

/** Range covering the finding's line, from the first non-blank character when the text is known. */
export function lineRange(f: Finding, doc?: vscode.TextDocument): vscode.Range {
  const line = Math.max(0, f.line - 1);
  if (doc && line < doc.lineCount) {
    const l = doc.lineAt(line);
    return new vscode.Range(line, l.firstNonWhitespaceCharacterIndex, line, l.range.end.character);
  }
  return new vscode.Range(line, 0, line, 10_000);
}

/** Recipe text -> Markdown: 4-space indented runs become python code blocks. */
export function recipeMarkdown(recipe: string): string {
  const out: string[] = [];
  let inCode = false;
  for (const raw of recipe.split("\n")) {
    const code = raw.startsWith("    ");
    if (code && !inCode) {
      out.push("", "```python");
      inCode = true;
    } else if (!code && inCode) {
      out.push("```", "");
      inCode = false;
    }
    out.push(code ? raw.slice(4) : raw);
  }
  if (inCode) {
    out.push("```");
  }
  return out.join("\n");
}

export function commandLink(command: string, args: unknown[]): string {
  return `command:${command}?${encodeURIComponent(JSON.stringify(args))}`;
}
