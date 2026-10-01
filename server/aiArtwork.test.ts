import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

test("ai artwork detection, defaults, and health-report note", () => {
  const proc = spawnSync("python3", ["server/ai_artwork_check.py"], {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 240000,
  });
  assert.equal(proc.status, 0, `${proc.stdout}\n${proc.stderr}`);
  assert.match(proc.stdout, /all ai artwork checks passed/);
});
