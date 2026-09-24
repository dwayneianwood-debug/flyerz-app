import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import {
  buildOfficeQuickCheck,
  clientSafeQuickCheckError,
} from "./fileProcessor";

test("quick check judges DPI against the chosen size and withholds a false print-ready tick", () => {
  const proc = spawnSync("python3", ["server/quick_check_rules_check.py"], {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 120000,
  });
  assert.equal(proc.status, 0, `${proc.stdout}\n${proc.stderr}`);
  assert.match(proc.stdout, /all quick-check rules passed/);
});

test("Word and PowerPoint uploads explain the problem without a server path", () => {
  for (const kind of ["docx", "pptx"]) {
    const result = buildOfficeQuickCheck(kind);
    const blob = JSON.stringify(result);
    assert.equal(result.allPassed, false);
    assert.ok(result.checks.every((check) => check.passed === false));
    assert.match(result.checks[0].message, /not ideal for litho printing/);
    assert.match(result.checks[0].message, /Please send a PDF or a picture \(JPG or PNG\)/);
    assert.doesNotMatch(blob, /\/workspace|\/uploads|cannot identify image file/i);
    const leaked = clientSafeQuickCheckError(
      `cannot identify image file '/workspace/uploads/${kind}-secret'`,
      kind,
    );
    assert.match(leaked, /Please send a PDF or a picture/);
    assert.doesNotMatch(leaked, /workspace|uploads/);
  }
});
