import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import {
  hiddenBleedEnabled,
  recoveredTrimMatches,
  sanitizeHiddenBleedChoice,
  shouldTryHiddenBleed,
} from "./hiddenBleed";

test("hidden bleed is off unless FLYERZ_HIDDEN_BLEED=1", () => {
  assert.equal(hiddenBleedEnabled({}), false);
  assert.equal(hiddenBleedEnabled({ FLYERZ_HIDDEN_BLEED: "0" }), false);
  assert.equal(hiddenBleedEnabled({ FLYERZ_HIDDEN_BLEED: "1" }), true);
  assert.equal(shouldTryHiddenBleed({ hiddenBleed: "use" }, "pdf", {}), false);
});

test("staff choice: use is the default, ignore turns it off, images are never touched", () => {
  const on = { FLYERZ_HIDDEN_BLEED: "1" };
  assert.equal(shouldTryHiddenBleed({}, "pdf", on), true);
  assert.equal(shouldTryHiddenBleed({ hiddenBleed: "use" }, "pdf", on), true);
  assert.equal(shouldTryHiddenBleed({ hiddenBleed: "ignore" }, "pdf", on), false);
  assert.equal(shouldTryHiddenBleed({}, "png", on), false);
  assert.equal(sanitizeHiddenBleedChoice("use"), "use");
  assert.equal(sanitizeHiddenBleedChoice("ignore"), "ignore");
  assert.equal(sanitizeHiddenBleedChoice("yes please"), undefined);
});

test("a recovered page is only kept when it matches the ordered size", () => {
  assert.equal(recoveredTrimMatches([148, 210], null, null), true);
  assert.equal(recoveredTrimMatches([148, 210], 148, 210), true);
  assert.equal(recoveredTrimMatches([148, 210], 210, 148), true);
  assert.equal(recoveredTrimMatches([148, 210], 90, 50), false);
  assert.equal(recoveredTrimMatches(undefined, 148, 210), false);
});

test("hidden bleed detection, recovery and fallbacks (python)", () => {
  const proc = spawnSync("python3", ["server/hidden_bleed_check.py"], {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 120000,
  });
  assert.equal(proc.status, 0, `${proc.stdout}\n${proc.stderr}`);
  assert.match(proc.stdout, /ALL HIDDEN BLEED CHECKS PASSED/);
});
