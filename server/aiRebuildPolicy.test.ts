import assert from "node:assert/strict";
import test from "node:test";

import { choosePressInput } from "./aiRebuildPolicy";

const audit = {
  aiRebuild: {
    detected: true,
    skipped: false,
    accepted: true,
    pdfPath: "/tmp/rebuilt.pdf",
  },
};

test("automatic compile uses an approved rebuild and ignores one that was not accepted", () => {
  const chosen = choosePressInput("auto", audit, "/tmp/original.png", (file) => file.endsWith("rebuilt.pdf"));
  assert.equal(chosen, "/tmp/rebuilt.pdf");
  const unapproved = choosePressInput(
    "auto",
    { aiRebuild: { ...audit.aiRebuild, accepted: false } },
    "/tmp/original.png",
    () => true,
  );
  assert.equal(unapproved, "/tmp/original.png");
  const pending = choosePressInput(
    "auto",
    { aiRebuild: { detected: true, skipped: false, pdfPath: "/tmp/rebuilt.pdf" } },
    "/tmp/original.png",
    () => true,
  );
  assert.equal(pending, "/tmp/original.png");
});

test("manual bleed styles keep the original file", () => {
  for (const strategy of ["mirror", "stretch", "replicate", "bgExtract", "upscale", "ai_outpaint", "colourBorder"]) {
    const chosen = choosePressInput(strategy, audit, "/tmp/original.png", () => true);
    assert.equal(chosen, "/tmp/original.png");
  }
});

test("skip and a missing pdf stay on the original", () => {
  const skipped = choosePressInput("auto", { aiRebuild: { ...audit.aiRebuild, skipped: true } }, "/tmp/original.png", () => true);
  assert.equal(skipped, "/tmp/original.png");
  const missing = choosePressInput("auto", audit, "/tmp/original.png", () => false);
  assert.equal(missing, "/tmp/original.png");
});
