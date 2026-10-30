// Builds the dagshift wheel from the repository root and copies it into engine/, where the
// extension installs it from on first run. Keeps the engine version locked to the extension.
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ext = resolve(here, "..");
const repo = resolve(ext, "..", "..");
const extVersion = JSON.parse(readFileSync(join(ext, "package.json"), "utf8")).version;
const pyproject = readFileSync(join(repo, "pyproject.toml"), "utf8");
const pyVersion = /^version\s*=\s*"([^"]+)"/m.exec(pyproject)?.[1];
if (pyVersion !== extVersion) {
  console.error(`version mismatch: pyproject.toml ${pyVersion} vs package.json ${extVersion}`);
  process.exit(1);
}
const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const dist = join(ext, ".engine-build");
rmSync(dist, { recursive: true, force: true });
execFileSync(python, ["-m", "build", "--wheel", "--outdir", dist, repo], { stdio: "inherit" });
const wheel = readdirSync(dist).find((f) => f.endsWith("-py3-none-any.whl"));
if (!wheel) {
  console.error("no wheel produced");
  process.exit(1);
}
const engineDir = join(ext, "engine");
rmSync(engineDir, { recursive: true, force: true });
mkdirSync(engineDir, { recursive: true });
copyFileSync(join(dist, wheel), join(engineDir, wheel));
rmSync(dist, { recursive: true, force: true });
console.log(`bundled ${wheel}`);
