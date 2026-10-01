import assert from "node:assert/strict";
import test from "node:test";
import { AUTOMATIC_BLEED_LABEL, bleedMethodChoices, pressReadyHeadline, shouldStartAutomaticCompile } from "./press-ready-ui.ts";

test("automatic stays first and the headline follows the press check", () => {
  const choices = bleedMethodChoices(["mirror", "colourBorder"]);
  assert.equal(choices[0], "auto");
  assert.deepEqual(choices.slice(1), ["mirror", "colourBorder"]);
  assert.equal(pressReadyHeadline({ passed: true, status: "ready" }), "Ready for press");
  assert.equal(pressReadyHeadline({ status: "needs-attention" }), "Needs attention");
  assert.equal(pressReadyHeadline({ status: "planned" }), AUTOMATIC_BLEED_LABEL);
  assert.equal(AUTOMATIC_BLEED_LABEL, "Automatic (recommended)");
  assert.equal(shouldStartAutomaticCompile({
    status: "complete",
    selected: "auto",
    canAssess: true,
    artworkGateReady: true,
  }), true);
  assert.equal(shouldStartAutomaticCompile({
    status: "complete",
    selected: "auto",
    canAssess: true,
    artworkGateReady: false,
  }), false);
  assert.equal(shouldStartAutomaticCompile({
    status: "complete",
    selected: "auto",
    pressStatus: "ready",
    artworkGateReady: true,
  }), false);
});
