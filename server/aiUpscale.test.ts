import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

test("ai upscale reuses Real-ESRGAN and enhance-then-bleed", () => {
  const proc = spawnSync("python3", ["server/ai_upscale_check.py"], {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 240000,
  });
  assert.equal(proc.status, 0, `${proc.stdout}\n${proc.stderr}`);
  assert.match(proc.stdout, /all ai upscale checks passed/);
});
