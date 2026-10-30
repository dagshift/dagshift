/**
 * Talks to a real `dagshift serve` process.
 * DAGSHIFT_PYTHON: a Python with dagshift installed (defaults to ../../.venv/bin/python).
 */
import assert from "node:assert/strict";
import { cpSync, existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { after, before, test } from "node:test";
import { EngineClient, EngineError } from "../src/engineClient";

const repo = resolve(__dirname, "..", "..", "..", "..");
const python = process.env.DAGSHIFT_PYTHON ?? resolve(repo, ".venv", "bin", "python");
const project = resolve(repo, "tests", "fixtures", "project");
const logs: string[] = [];
const client = new EngineClient({ command: python, args: ["-m", "dagshift", "serve"], log: (l) => logs.push(l) });

before(() => {
  assert.ok(existsSync(python), `python not found: ${python}`);
});

after(async () => {
  await client.dispose();
});

test("initialize reports targets and rules", async () => {
  const info = await client.start();
  assert.deepEqual(info.targets.map((t) => t.target), ["2.7", "2.8", "2.9", "2.10", "2.11", "3.0", "3.1", "3.2", "3.3"]);
  assert.ok(info.sources.includes("2.4"));
  assert.ok(info.rules.DS401);
});

test("check returns absolute paths and findings", async () => {
  const res = await client.check(project, { startDate: "2026-10-29" });
  assert.equal(res.target, "3.3");
  const classic = res.files.find((f) => f.path.endsWith("dags/classic_operators.py"));
  assert.ok(classic?.changed);
  assert.ok(classic.findings.some((f) => f.rule_id === "DS201" && f.severity === "fixed"));
  const metadata = res.files.find((f) => f.path.endsWith("dags/metadata_access.py"));
  assert.ok(metadata?.findings.some((f) => f.severity === "manual" && f.recipe));
});

test("detect reads pinned versions, including from extra paths", async () => {
  const det = await client.detect(project, []);
  assert.equal(det.version, "2.10.5");
  assert.equal(det.confidence, "pinned");
  const dagsOnly = await client.detect(resolve(project, "dags"), []);
  assert.notEqual(dagsOnly.confidence, "pinned");
  const withInfra = await client.detect(resolve(project, "dags"), [resolve(project, "infra")]);
  assert.equal(withInfra.minor, "2.10");
});

test("check reports the current and target versions", async () => {
  const res = await client.check(project, { target: "2.11" });
  assert.equal(res.source, "2.10");
  assert.equal(res.target, "2.11");
  const explicit = await client.check(project, { source: "2.8", target: "3.1" });
  assert.equal(explicit.source, "2.8");
  assert.equal(explicit.detection, null);
});

test("migrateDocument uses unsaved text and the requested target", async () => {
  const path = resolve(project, "dags", "classic_operators.py");
  const text = readFileSync(path, "utf8") + "\nfrom airflow.utils.trigger_rule import TriggerRule\n";
  const on33 = await client.migrateDocument(path, text, project, { target: "3.3" });
  assert.ok(on33.migrated?.includes("TriggerRule"));
  assert.ok(on33.migrated?.includes("from airflow.sdk import"));
  const on30 = await client.migrateDocument(path, text, project, { target: "3.0" });
  const on210 = await client.migrateDocument(path, text, project, { source: "2.7", target: "2.10" });
  assert.ok(!on210.migrated?.includes("airflow.sdk"));
  assert.ok(on210.migrated?.includes("from airflow.operators.bash import BashOperator"));
  assert.ok(on30.migrated?.includes("from airflow.utils.trigger_rule import TriggerRule"));
});

test("writeCopy upgrades into a separate folder and leaves the originals alone", async () => {
  const work = mkdtempSync(resolve(tmpdir(), "dagshift-copy-"));
  const src = resolve(work, "project");
  cpSync(project, src, { recursive: true });
  const original = readFileSync(resolve(src, "dags", "classic_operators.py"), "utf8");
  const out = resolve(work, "project-airflow-3.3");
  const res = await client.writeCopy({ root: src, outDir: out, options: { startDate: "2026-10-29" } });
  assert.ok(res.ok);
  assert.equal(res.target, "3.3");
  assert.equal(readFileSync(resolve(src, "dags", "classic_operators.py"), "utf8"), original);
  const classic = res.changed.find((c) => c.relPath === "dags/classic_operators.py");
  assert.ok(classic && readFileSync(classic.upgraded, "utf8").includes("airflow.providers.standard"));
  const again = await client.writeCopy({ root: src, outDir: out, options: {} });
  assert.equal(again.ok, false);
  assert.equal(!again.ok && again.reason, "earlier-copy");
});

test("unknown option surfaces as EngineError and the server keeps running", async () => {
  await assert.rejects(client.check(project, { nope: true }), (err: unknown) => err instanceof EngineError);
  const again = await client.migrateDocument(resolve(project, "x.py"), "x = 1\n", project, {});
  assert.equal(again.changed, false);
});

test("a crashed engine restarts on the next request", async () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (client as any).proc.kill("SIGKILL");
  await new Promise((r) => setTimeout(r, 300));
  assert.equal(client.running, false);
  const res = await client.migrateDocument(resolve(project, "y.py"), "from airflow import DAG\n", project, {});
  assert.ok(res.changed);
  assert.ok(logs.some((l) => l.includes("exited")));
});
