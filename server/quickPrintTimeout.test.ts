import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { deadChildResult, runQuickPrintFile, tooLongMessage } from "./quickPrintRunner.ts";

test("a timeout names the size and a dead file still says it could not be opened", () => {
  const slow = deadChildResult(true, 200 * 1024 * 1024);
  assert.equal(slow.clientMessage, "This file took too long to process (200 MB); we're checking it manually");
  assert.equal(slow.clientMessage, tooLongMessage(200 * 1024 * 1024));
  assert.equal(slow.clientMessage.includes("could not open"), false);
  const dead = deadChildResult(false, 200 * 1024 * 1024);
  assert.match(dead.clientMessage, /could not open this file/i);
});

test("a 200 MB job that overruns says so, and the clock is not the open-file message", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-slow-"));
  const input = path.join(root, "big.pdf");
  const script = path.join(root, "sleep.py");
  fs.writeFileSync(input, "%PDF-1.4\n");
  fs.truncateSync(input, 200 * 1024 * 1024);
  fs.writeFileSync(script, "import time\ntime.sleep(30)\n");
  const previousScript = process.env.QUICK_PRINT_SCRIPT;
  const previousKill = process.env.QUICK_PRINT_KILL_MS;
  process.env.QUICK_PRINT_SCRIPT = script;
  process.env.QUICK_PRINT_KILL_MS = "400";
  try {
    const started = Date.now();
    const result = await runQuickPrintFile(input, path.join(root, "out"), {
      productId: "a5",
      filename: "big.pdf",
    });
    assert.ok(Date.now() - started < 5000);
    assert.equal(result.clientMessage, "This file took too long to process (200 MB); we're checking it manually");
  } finally {
    if (previousScript === undefined) delete process.env.QUICK_PRINT_SCRIPT;
    else process.env.QUICK_PRINT_SCRIPT = previousScript;
    if (previousKill === undefined) delete process.env.QUICK_PRINT_KILL_MS;
    else process.env.QUICK_PRINT_KILL_MS = previousKill;
  }
});
