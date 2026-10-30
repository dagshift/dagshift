/**
 * Finds or creates the Python environment that runs the dagshift engine.
 *
 * Order:
 *   1. `dagshift.interpreter`: a Python that already has dagshift installed.
 *   2. The extension's managed environment in global storage, created once from the wheel bundled
 *      inside the extension (so the engine version always matches the extension version).
 */
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

export interface EngineCommand {
  command: string;
  args: string[];
  version: string;
  source: "interpreter" | "managed";
}

interface PythonCandidate {
  cmd: string;
  pre: string[];
  label: string;
}

const IS_WIN = process.platform === "win32";

function run(
  cmd: string,
  args: string[],
  timeout = 60_000,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    execFile(
      cmd,
      args,
      { timeout, maxBuffer: 16 * 1024 * 1024, windowsHide: true, env: { ...process.env, PIP_DISABLE_PIP_VERSION_CHECK: "1" } },
      (err, stdout, stderr) => {
        if (err) {
          const detail = (stderr || stdout || err.message).toString().trim().split("\n").slice(-6).join("\n");
          reject(new Error(`${path.basename(cmd)} ${args.join(" ")} failed: ${detail}`));
        } else {
          resolve({ stdout: stdout.toString(), stderr: stderr.toString() });
        }
      },
    );
  });
}

export function venvPython(dir: string): string {
  return IS_WIN ? path.join(dir, "Scripts", "python.exe") : path.join(dir, "bin", "python");
}

async function pythonVersion(c: PythonCandidate): Promise<[number, number] | undefined> {
  try {
    const { stdout } = await run(c.cmd, [...c.pre, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], 15_000);
    const [maj, min] = stdout.trim().split(".").map(Number);
    return Number.isFinite(maj) && Number.isFinite(min) ? [maj, min] : undefined;
  } catch {
    return undefined;
  }
}

export async function dagshiftVersion(python: string): Promise<string | undefined> {
  try {
    const { stdout } = await run(python, ["-c", "import dagshift;print(dagshift.__version__)"], 30_000);
    return stdout.trim() || undefined;
  } catch {
    return undefined;
  }
}

async function pythonExtensionInterpreter(resource?: vscode.Uri): Promise<string | undefined> {
  const ext = vscode.extensions.getExtension("ms-python.python");
  if (!ext) {
    return undefined;
  }
  try {
    if (!ext.isActive) {
      await ext.activate();
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const api: any = ext.exports;
    const active = api?.environments?.getActiveEnvironmentPath?.(resource);
    if (active?.path && fs.existsSync(active.path) && fs.statSync(active.path).isFile()) {
      return active.path as string;
    }
    const details = api?.settings?.getExecutionDetails?.(resource);
    const cmd = details?.execCommand?.[0];
    return typeof cmd === "string" && cmd ? cmd : undefined;
  } catch {
    return undefined;
  }
}

async function findBasePython(log: vscode.LogOutputChannel, resource?: vscode.Uri): Promise<PythonCandidate> {
  const candidates: PythonCandidate[] = [];
  const configured = vscode.workspace.getConfiguration("dagshift", resource).get<string>("bootstrapPython")?.trim();
  if (configured) {
    candidates.push({ cmd: configured, pre: [], label: configured });
  }
  const fromExt = await pythonExtensionInterpreter(resource);
  if (fromExt) {
    candidates.push({ cmd: fromExt, pre: [], label: `${fromExt} (Python extension)` });
  }
  // Best-tested versions first, then newer ones, then whatever the launcher or PATH defaults to.
  if (IS_WIN) {
    for (const v of ["3.13", "3.12", "3.11", "3.10", "3.14"]) {
      candidates.push({ cmd: "py", pre: [`-${v}`], label: `py -${v}` });
    }
    candidates.push(
      { cmd: "py", pre: ["-3"], label: "py -3" },
      { cmd: "python", pre: [], label: "python" },
      { cmd: "python3", pre: [], label: "python3" },
    );
  } else {
    for (const name of ["python3.13", "python3.12", "python3.11", "python3.10", "python3.14", "python3", "python"]) {
      candidates.push({ cmd: name, pre: [], label: name });
    }
  }
  const tried: string[] = [];
  for (const c of candidates) {
    const v = await pythonVersion(c);
    tried.push(`${c.label}: ${v ? v.join(".") : "not found"}`);
    if (v && (v[0] > 3 || (v[0] === 3 && v[1] >= 10))) {
      log.info(`Using ${c.label} (Python ${v.join(".")}) to create the engine environment`);
      return c;
    }
  }
  throw new Error(
    "dagshift needs Python 3.10 or newer to run its engine, and none was found. " +
      `Tried: ${tried.join("; ")}. Install Python 3.10+ or set "dagshift.bootstrapPython".`,
  );
}

export function bundledWheel(extensionPath: string): { file: string; version: string } | undefined {
  const dir = path.join(extensionPath, "engine");
  if (!fs.existsSync(dir)) {
    return undefined;
  }
  const wheel = fs.readdirSync(dir).find((f) => /^dagshift-[^-]+-py3-none-any\.whl$/.test(f));
  if (!wheel) {
    return undefined;
  }
  return { file: path.join(dir, wheel), version: wheel.split("-")[1] };
}

export class EngineInstaller {
  private inflight?: Promise<EngineCommand>;

  constructor(
    private readonly ctx: vscode.ExtensionContext,
    private readonly log: vscode.LogOutputChannel,
  ) {}

  resolve(force = false): Promise<EngineCommand> {
    if (!this.inflight || force) {
      this.inflight = this.doResolve(force).catch((err) => {
        this.inflight = undefined;
        throw err;
      });
    }
    return this.inflight;
  }

  private async doResolve(force: boolean): Promise<EngineCommand> {
    const interpreter = vscode.workspace.getConfiguration("dagshift").get<string>("interpreter")?.trim();
    if (interpreter) {
      const v = await dagshiftVersion(interpreter);
      if (!v) {
        throw new Error(
          `"dagshift.interpreter" is ${interpreter}, but the dagshift engine is not installed there. ` +
            `Clear the setting to let the extension manage its own engine (recommended), or install it with: ` +
            `${interpreter} -m pip install "git+https://github.com/dagshift/dagshift"`,
        );
      }
      this.log.info(`Using dagshift ${v} from ${interpreter}`);
      return { command: interpreter, args: ["-m", "dagshift", "serve"], version: v, source: "interpreter" };
    }

    const wheel = bundledWheel(this.ctx.extensionPath);
    const want: string = wheel?.version ?? this.ctx.extension.packageJSON.version;
    const base = this.ctx.globalStorageUri.fsPath;
    const envDir = path.join(base, `engine-${want}`);
    const py = venvPython(envDir);
    const marker = path.join(envDir, ".dagshift-wheel");
    const fingerprint = wheel ? createHash("sha256").update(fs.readFileSync(wheel.file)).digest("hex") : want;
    const installed = fs.existsSync(marker) ? fs.readFileSync(marker, "utf8").trim() : "";
    if (!force && fs.existsSync(py) && installed === fingerprint && (await dagshiftVersion(py)) === want) {
      return { command: py, args: ["-m", "dagshift", "serve"], version: want, source: "managed" };
    }

    return vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "dagshift: setting up the migration engine (one time)" },
      async (progress) => {
        fs.rmSync(envDir, { recursive: true, force: true });
        fs.mkdirSync(base, { recursive: true });
        progress.report({ message: "finding Python 3.10+" });
        const basePy = await findBasePython(this.log, vscode.workspace.workspaceFolders?.[0]?.uri);

        progress.report({ message: "creating environment" });
        let uv: string | undefined;
        try {
          await run(basePy.cmd, [...basePy.pre, "-m", "venv", envDir], 300_000);
        } catch (err) {
          this.log.warn(`python -m venv failed: ${(err as Error).message}`);
          uv = await this.findUv();
          if (!uv) {
            throw new Error(
              `Could not create a virtual environment with ${basePy.label}. On Debian/Ubuntu install the ` +
                `python3-venv package, or set "dagshift.interpreter" to a Python that has dagshift installed.`,
            );
          }
          fs.rmSync(envDir, { recursive: true, force: true });
          await run(uv, ["venv", "--python", basePy.cmd, envDir], 300_000);
        }

        progress.report({ message: "installing dagshift" });
        if (!wheel) {
          throw new Error(
            "This build of the extension has no bundled engine. Reinstall dagshift from the Marketplace, or " +
              "when running from source, run `npm run bundle-engine` in editors/vscode first.",
          );
        }
        const spec = wheel.file;
        this.log.info(`Installing ${spec} into ${envDir}`);
        if (uv) {
          await run(uv, ["pip", "install", "--python", py, spec], 900_000);
        } else {
          await run(py, ["-m", "pip", "install", "--no-input", spec], 900_000);
        }
        const got = await dagshiftVersion(py);
        if (!got) {
          throw new Error(`dagshift did not import after installation into ${envDir}. See the dagshift log.`);
        }
        fs.writeFileSync(marker, fingerprint, "utf8");
        this.removeOldEngines(base, path.basename(envDir));
        this.log.info(`Engine ready: dagshift ${got} in ${envDir}`);
        return { command: py, args: ["-m", "dagshift", "serve"], version: got, source: "managed" as const };
      },
    );
  }

  private async findUv(): Promise<string | undefined> {
    try {
      await run("uv", ["--version"], 10_000);
      return "uv";
    } catch {
      return undefined;
    }
  }

  private removeOldEngines(base: string, keep: string): void {
    for (const name of fs.readdirSync(base)) {
      if (name.startsWith("engine-") && name !== keep) {
        try {
          fs.rmSync(path.join(base, name), { recursive: true, force: true });
        } catch {
          // in use on Windows; next activation retries
        }
      }
    }
  }
}
