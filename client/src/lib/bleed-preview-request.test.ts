import assert from "node:assert/strict";
import test from "node:test";
import { bleedPreviewQuery } from "./bleed-preview-request.ts";
import { normalizeBleedMm } from "../../../shared/bleed-size.ts";

test("bleed preview query ignores a click event and keeps the chosen bleed", () => {
  const click = { type: "click", target: {} };
  const query = bleedPreviewQuery(click, 10);
  assert.equal(query, "?bleed=10");
  assert.equal(bleedPreviewQuery("mirror", 3), "?strategy=mirror&bleed=3");
  assert.equal(bleedPreviewQuery("auto", "7.2"), "?bleed=7.2");
  assert.equal(normalizeBleedMm("nope"), 5);
  assert.equal(normalizeBleedMm(40), 5);
});
