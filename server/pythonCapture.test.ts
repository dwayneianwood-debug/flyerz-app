import test from "node:test";
import assert from "node:assert/strict";
import { execPythonAsync } from "./pythonCapture.ts";

test("cat python leaves the event loop free and a timeout still answers", async () => {
  let ticks = 0;
  const timer = setInterval(() => {
    ticks += 1;
  }, 40);
  const started = Date.now();
  try {
    await assert.rejects(
      () => execPythonAsync(["-c", "import time; time.sleep(5); print('{}')"], "GlitchyDesigner", 350),
      /timed out after 0\.35 seconds/,
    );
    assert.ok(Date.now() - started < 2000);
    assert.ok(ticks >= 2, `ticks ${ticks}`);
    const payload = await execPythonAsync(
      ["-c", "print('{\"ok\": true, \"reply\": \"ready\"}')"],
      "GlitchyDesigner",
      5000,
    );
    assert.equal(payload.reply, "ready");
  } finally {
    clearInterval(timer);
  }
});
