import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { MARKER, displayFolder, folderState, insideUpgradedCopy, outputFolderFor } from "../src/output";

test("the default copy sits next to the upgraded folder", () => {
  assert.equal(outputFolderFor("/repos/orch", "2.10", "3.3"), "/repos/orch-airflow-3.3");
  assert.equal(outputFolderFor("/repos/orch/composer", "2.10", "3.3"), "/repos/orch/composer-airflow-3.3");
  assert.equal(outputFolderFor("/repos/orch", "2.10", "3.1", "upgraded/${source}-to-${target}"), "/repos/upgraded/2.10-to-3.1");
  assert.equal(outputFolderFor("/repos/orch", null, "3.3", "/tmp/out/${folderName}"), "/tmp/out/orch");
});

test("output folders are shown relative to the upgraded folder when close by", () => {
  assert.equal(displayFolder("/repos/orch-airflow-3.3", "/repos/orch"), "../orch-airflow-3.3");
  assert.equal(displayFolder("/repos/orch/upgraded", "/repos/orch"), "upgraded");
  assert.equal(displayFolder("/elsewhere/x", "/repos/orch"), "/elsewhere/x");
});

test("folder state tells empty, earlier copies and other folders apart", () => {
  const dir = mkdtempSync(join(tmpdir(), "dagshift-out-"));
  assert.equal(folderState(join(dir, "nope")), "missing");
  mkdirSync(join(dir, "empty"));
  assert.equal(folderState(join(dir, "empty")), "empty");
  mkdirSync(join(dir, "copy"));
  writeFileSync(join(dir, "copy", MARKER), "{}");
  assert.equal(folderState(join(dir, "copy")), "earlier-copy");
  mkdirSync(join(dir, "mine"));
  writeFileSync(join(dir, "mine", "notes.txt"), "keep");
  assert.equal(folderState(join(dir, "mine")), "other");
});

test("files in an upgraded copy are recognised, a copy opened as the workspace is not", () => {
  const dir = mkdtempSync(join(tmpdir(), "dagshift-copy-"));
  const ws = join(dir, "repo");
  mkdirSync(join(ws, "upgraded", "dags"), { recursive: true });
  writeFileSync(join(ws, "upgraded", MARKER), "{}");
  mkdirSync(join(ws, "dags"));
  assert.equal(insideUpgradedCopy(join(ws, "upgraded", "dags", "a.py"), ws), true);
  assert.equal(insideUpgradedCopy(join(ws, "dags", "a.py"), ws), false);
  assert.equal(insideUpgradedCopy(join(ws, "upgraded", "dags", "a.py"), join(ws, "upgraded")), false);
  assert.equal(insideUpgradedCopy(join(ws, "upgraded", "dags", "a.py")), true);
});
