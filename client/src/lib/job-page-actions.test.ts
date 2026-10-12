import assert from "node:assert/strict";
import test from "node:test";
import { displayPercent } from "@shared/jobProgress";
import { PRESS_FILE_MISSING, PRESS_READY_FILENAME, autoPostsForJobPage, jobPageMayAutoStart, proceedButtonState, proceedPressDownload, proceedPressResult } from "./job-page-actions";
import { shouldStartAutomaticCompile } from "./press-ready-ui";

test("opening a job page mid-run or after it finishes starts nothing", () => {
  for (const status of ["processing", "complete"]) {
    assert.equal(jobPageMayAutoStart(status), false);
    assert.deepEqual(autoPostsForJobPage(status), []);
    assert.equal(shouldStartAutomaticCompile({
      status,
      selected: "auto",
      artworkGateReady: true,
      hasExistingPress: status === "complete",
    }), false);
  }
});

test("a processing percent never sits on 95", () => {
  assert.equal(displayPercent("processing", 95), 90);
  assert.equal(displayPercent("processing", undefined), 8);
  assert.equal(displayPercent("processing", 28), 28);
  assert.equal(displayPercent("complete", 90), 100);
});

test("the proceed button spins only while work is running", () => {
  const waiting = proceedButtonState({
    allReviewsChecked: true,
    selectedBleedMethod: "auto",
    prepressSpinnerActive: false,
    preCompileState: null,
    preCompileReady: false,
    hasAutomaticPress: false,
  });
  assert.equal(waiting.spinning, false);
  assert.equal(waiting.disabled, true);
  assert.equal(waiting.label, "Choose a bleed method to continue");

  const compiling = proceedButtonState({
    allReviewsChecked: true,
    selectedBleedMethod: "auto",
    prepressSpinnerActive: false,
    preCompileState: "compiling",
    preCompileReady: false,
    hasAutomaticPress: false,
  });
  assert.equal(compiling.spinning, true);
  assert.match(compiling.label, /Preparing artwork/);

  const quick = proceedButtonState({
    allReviewsChecked: true,
    selectedBleedMethod: "auto",
    prepressSpinnerActive: false,
    preCompileState: null,
    preCompileReady: false,
    hasAutomaticPress: true,
  });
  assert.equal(quick.spinning, false);
  assert.equal(quick.disabled, false);
  assert.equal(quick.label, "Proceed to Download");

  const review = proceedButtonState({
    allReviewsChecked: false,
    selectedBleedMethod: "mirror",
    prepressSpinnerActive: false,
    preCompileState: "ready",
    preCompileReady: true,
    hasAutomaticPress: false,
  });
  assert.equal(review.spinning, false);
  assert.match(review.label, /Review/);
});

test("proceed to download fetches the press PDF and stays quiet when it is missing", () => {
  const plan = proceedPressDownload(1270);
  assert.equal(plan.url, "/api/jobs/1270/download/press-ready");
  assert.equal(plan.filename, PRESS_READY_FILENAME);
  assert.equal(plan.filename.endsWith(".pdf"), true);

  const ready = proceedPressResult(true);
  assert.equal(ready.notifyCat, true);
  assert.equal(ready.message, null);

  const missing = proceedPressResult(false, "");
  assert.equal(missing.notifyCat, false);
  assert.equal(missing.message, PRESS_FILE_MISSING);

  const server = proceedPressResult(false, PRESS_FILE_MISSING);
  assert.equal(server.notifyCat, false);
  assert.equal(server.message, "There isn't a press-ready PDF for this job yet.");
});
