import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AiArtworkPanelView } from "./ai-artwork-panel";
import { coverCropFrame, ratiosDiffer } from "../lib/ai-artwork-fit";

const noop = () => {};

test("crop to fit is the default and the safe zone is shown", () => {
  const frame = coverCropFrame(1024, 1024, 148, 210, 0.5);
  assert.equal(ratiosDiffer(1024, 1024, 148, 210), true);
  assert.equal(frame.axis, "x");
  assert.ok(frame.left > 0 && frame.left < 40);
  assert.ok(Math.abs(frame.safeY - (5 / 210) * 100) < 0.01);

  const html = renderToStaticMarkup(
    React.createElement(AiArtworkPanelView, {
      plan: {
        detected: true,
        mismatch: true,
        fit: "crop",
        offset: 0.5,
        bleed: "mirror",
        srcW: 1024,
        srcH: 1024,
        sourceUrl: "/preview.png",
        bright: true,
        brightMessage: "Some bright screen colours will look duller in print.",
        screenUrl: "/screen.png",
        printUrl: "/print.png",
        textStatus: "warning",
        textMessage: "Possible text problem: 'SOON' → 'soon'. The job was not stopped.",
        applied: ["crop to fit, with the safe zone shown", "CMYK conversion for litho print"],
        reasons: ["1024×1024 pixels matches a common AI image size"],
      },
      trimWidthMm: 148,
      trimHeightMm: 210,
      onFit: noop,
      onOffset: noop,
    }),
  );

  assert.match(html, /AI-generated artwork detected/);
  assert.match(html, /aria-pressed="true"/);
  assert.match(html, /Crop to fit/);
  assert.match(html, /Extend the background/);
  assert.match(html, /Colour border/);
  assert.match(html, /data-testid="ai-artwork-safe-zone"/);
  assert.match(html, /Safe zone/);
  assert.match(html, /not stopped/);
  assert.match(html, /duller in print/);
  assert.match(html, /data-testid="img-ai-soft-proof"/);
  assert.equal(html.includes("blocked"), false);
});
