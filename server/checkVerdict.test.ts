import assert from "node:assert/strict";
import test from "node:test";
import { checksAllPassed } from "./checkVerdict";

test("a warning or manual review does not count as a pass", () => {
  assert.equal(checksAllPassed([{ passed: false, severity: "WARNING" }]), false);
  assert.equal(checksAllPassed([{ passed: false, severity: "MANUAL_REVIEW" }]), false);
  assert.equal(checksAllPassed([{ passed: true, severity: "WARNING" }]), true);
  assert.equal(checksAllPassed([{ passed: true }, { passed: true }]), true);
});
