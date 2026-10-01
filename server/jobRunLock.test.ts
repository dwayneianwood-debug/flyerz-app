import assert from "node:assert/strict";
import test from "node:test";
import { beginJobRun, currentJobRun, duplicateRun, endJobRun } from "./jobRunLock";

test("a second start joins the run and does not take the lock", () => {
  const jobId = 222;
  endJobRun(jobId, "quick");
  const first = beginJobRun(jobId, "quick");
  assert.equal(first.started, true);
  const second = beginJobRun(jobId, "process");
  assert.equal(second.started, false);
  if (!second.started) assert.equal(second.current.owner, "quick");
  assert.equal(duplicateRun("processing", jobId), true);
  assert.equal(duplicateRun("complete", jobId), true);
  assert.equal(currentJobRun(jobId)?.owner, "quick");
  endJobRun(jobId, "process");
  assert.equal(currentJobRun(jobId)?.owner, "quick");
  endJobRun(jobId, "quick");
  assert.equal(currentJobRun(jobId), null);
  assert.equal(duplicateRun("complete", jobId), false);
  assert.equal(duplicateRun("processing", jobId), true);
});
