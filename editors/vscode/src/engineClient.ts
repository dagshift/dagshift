/**
 * Client for `dagshift serve`: line-delimited JSON over stdio.
 *
 * No `vscode` import here, so it can be tested with plain Node.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import * as readline from "node:readline";

export type Severity = "fixed" | "manual" | "warn" | "info";

export interface Finding {
  rule_id: string;
  severity: Severity;
  path: string;
  line: number;
  message: string;
  recipe: string | null;
  rule_name: string;
  absPath: string | null;
}

export interface FileReport {
  path: string;
  relPath: string;
  kind: string | null;
  changed: boolean;
  templated: boolean;
  error: string | null;
  findings: Finding[];
  migrated?: string | null;
}

export interface Summary {
  files_scanned: number;
  files_changed: number;
  fixed: number;
  manual: number;
  warn: number;
  info: number;
  errors: number;
}

export interface UpgradedFile {
  relPath: string;
  original: string;
  upgraded: string;
  fixes: number;
  manual: number;
}

export type WriteCopyResult =
  | { ok: true; outDir: string; source: string | null; target: string; summary: Summary; changed: UpgradedFile[]; report: string | null }
  | { ok: false; reason: "not-empty" | "earlier-copy" | "not-a-folder"; message: string; outDir: string };

export interface Evidence {
  version: string;
  file: string;
  line: number;
  kind: string;
  text: string;
}

export interface Detection {
  version: string | null;
  minor: string | null;
  confidence: "pinned" | "inferred" | "unknown";
  evidence: Evidence[];
  conflicts: string[];
  note: string;
}

export interface CheckResult {
  target: string;
  source: string | null;
  detection: Detection | null;
  verifiedAgainst: string;
  summary: Summary;
  files: FileReport[];
  projectFindings: Finding[];
}

export interface TargetInfo {
  target: string;
  verifiedAgainst: string;
}

export interface InitializeResult {
  version: string;
  targets: TargetInfo[];
  sources: string[];
  defaultTarget: string;
  rules: Record<string, { name: string; help: string }>;
}

export interface ValidateResult {
  ok: boolean;
  airflowVersion: string | null;
  importErrors: Record<string, string>;
  dags: Record<string, { file: string; tasks: number; timetable: string }>;
  warnings: { file: string; line: number; category: string; message: string }[];
  fatal: string | null;
  text: string;
}

export type EngineOptions = Record<string, unknown>;

export class EngineError extends Error {
  constructor(message: string, readonly type: string, readonly trace?: string) {
    super(message);
    this.name = "EngineError";
  }
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: NodeJS.Timeout;
  method: string;
}

export interface EngineClientOptions {
  command: string;
  args: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  log?: (line: string) => void;
}

export class EngineClient {
  private proc?: ChildProcessWithoutNullStreams;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private starting?: Promise<InitializeResult>;
  private disposed = false;
  info?: InitializeResult;

  constructor(private readonly opts: EngineClientOptions) {}

  get running(): boolean {
    return this.proc !== undefined && this.proc.exitCode === null;
  }

  /** Spawn the server (once) and return its initialize result. */
  start(): Promise<InitializeResult> {
    if (this.disposed) {
      return Promise.reject(new Error("engine client disposed"));
    }
    if (this.starting) {
      return this.starting;
    }
    const log = this.opts.log ?? (() => undefined);
    this.starting = new Promise<InitializeResult>((resolve, reject) => {
      let proc: ChildProcessWithoutNullStreams;
      try {
        proc = spawn(this.opts.command, this.opts.args, {
          cwd: this.opts.cwd,
          env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1", ...this.opts.env },
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
        });
      } catch (err) {
        this.starting = undefined;
        reject(err as Error);
        return;
      }
      this.proc = proc;
      log(`engine: started ${this.opts.command} ${this.opts.args.join(" ")} (pid ${proc.pid})`);
      proc.stdin.setDefaultEncoding("utf8");
      readline.createInterface({ input: proc.stdout }).on("line", (line) => this.onLine(line));
      readline.createInterface({ input: proc.stderr }).on("line", (line) => log(`engine: ${line}`));
      proc.on("error", (err) => {
        log(`engine: failed to start: ${err.message}`);
        this.failAll(err);
        this.proc = undefined;
        this.starting = undefined;
        reject(err);
      });
      proc.on("exit", (code, signal) => {
        log(`engine: exited (code ${code}, signal ${signal})`);
        this.failAll(new Error(`dagshift engine exited (code ${code}${signal ? `, ${signal}` : ""})`));
        if (this.proc === proc) {
          this.proc = undefined;
          this.starting = undefined;
        }
      });
      this.send<InitializeResult>("initialize", {}, 60_000)
        .then((info) => {
          this.info = info;
          resolve(info);
        })
        .catch((err: Error) => {
          this.starting = undefined;
          reject(err);
        });
    });
    return this.starting;
  }

  async request<T>(method: string, params: Record<string, unknown> = {}, timeoutMs = 120_000): Promise<T> {
    await this.start();
    return this.send<T>(method, params, timeoutMs);
  }

  private send<T>(method: string, params: Record<string, unknown>, timeoutMs: number): Promise<T> {
    const proc = this.proc;
    if (!proc) {
      return Promise.reject(new Error("dagshift engine is not running"));
    }
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`dagshift engine: ${method} timed out after ${Math.round(timeoutMs / 1000)}s`));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer, method });
      proc.stdin.write(JSON.stringify({ id, method, params }) + "\n", (err) => {
        if (err) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(err);
        }
      });
    });
  }

  private onLine(line: string): void {
    const text = line.trim();
    if (!text) {
      return;
    }
    let msg: { id?: number; result?: unknown; error?: { message: string; type: string; trace?: string } };
    try {
      msg = JSON.parse(text);
    } catch {
      this.opts.log?.(`engine (non-JSON output): ${text}`);
      return;
    }
    if (typeof msg.id !== "number") {
      return;
    }
    const pending = this.pending.get(msg.id);
    if (!pending) {
      return;
    }
    this.pending.delete(msg.id);
    clearTimeout(pending.timer);
    if (msg.error) {
      pending.reject(new EngineError(msg.error.message, msg.error.type, msg.error.trace));
    } else {
      pending.resolve(msg.result);
    }
  }

  private failAll(err: Error): void {
    for (const [id, p] of this.pending) {
      clearTimeout(p.timer);
      p.reject(err);
      this.pending.delete(id);
    }
  }

  async stop(): Promise<void> {
    const proc = this.proc;
    if (!proc) {
      return;
    }
    try {
      await this.send("shutdown", {}, 3_000);
    } catch {
      // fall through to kill
    }
    if (proc.exitCode === null) {
      proc.kill();
    }
    this.proc = undefined;
    this.starting = undefined;
  }

  async dispose(): Promise<void> {
    await this.stop();
    this.disposed = true;
  }

  // ---- typed helpers -----------------------------------------------------------------
  detect(root: string, extraPaths: string[]): Promise<Detection> {
    return this.request<Detection>("detect", { root, extraPaths }, 5 * 60_000);
  }

  check(root: string, options: EngineOptions, paths?: string[]): Promise<CheckResult> {
    return this.request<CheckResult>("check", { root, paths, options }, 30 * 60_000);
  }

  migrateDocument(path: string, text: string | undefined, root: string | undefined, options: EngineOptions): Promise<FileReport> {
    return this.request<FileReport>("migrateDocument", { path, text, root, options }, 120_000);
  }

  migrateFiles(root: string, files: { path: string; text?: string }[], options: EngineOptions): Promise<{ files: FileReport[] }> {
    return this.request<{ files: FileReport[] }>("migrateFiles", { root, files, options }, 30 * 60_000);
  }

  /** Upgrade `path` (default: root) into outDir; the originals are not touched. */
  writeCopy(params: { root: string; path?: string; outDir: string; options: EngineOptions; replace?: boolean; writeReport?: boolean }): Promise<WriteCopyResult> {
    return this.request<WriteCopyResult>("writeCopy", params, 30 * 60_000);
  }

  report(root: string, format: "md" | "json" | "sarif" | "text", options: EngineOptions): Promise<{ text: string; summary: Summary }> {
    return this.request("report", { root, format, options }, 30 * 60_000);
  }

  validate(params: { dagFolder: string; airflowPython: string; renderCmd?: string; pythonpath?: string[] }): Promise<ValidateResult> {
    return this.request<ValidateResult>("validate", params, 30 * 60_000);
  }
}
