import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

test("a value only present in .env reaches a spawned Python process", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-dotenv-"));
  fs.writeFileSync(
    path.join(dir, ".env"),
    "FLYERZ_DOTENV_PROBE=from-dotenv\nFLYERZ_DOTENV_KEEP=from-dotenv\n",
    "utf8",
  );
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env.FLYERZ_DOTENV_PROBE;
  env.FLYERZ_DOTENV_KEEP = "from-process";

  const proc = spawnSync(
    path.join(process.cwd(), "node_modules", ".bin", "tsx"),
    [path.join(process.cwd(), "server", "loadEnvProbe.ts")],
    { cwd: dir, env, encoding: "utf8", timeout: 30000 },
  );

  assert.equal(proc.status, 0, `${proc.stdout}\n${proc.stderr}`);
  const [probe, keep] = (proc.stdout || "").trim().split(/\r?\n/);
  assert.equal(probe, "from-dotenv");
  assert.equal(keep, "from-process");
});
