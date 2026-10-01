import assert from "node:assert/strict";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AI_UPSCALE_EXPLANATION, AiUpscalePanelView } from "./ai-upscale-panel";

const noop = () => {};

test("upscale step explains itself and stays off until the user opts in", () => {
  const html = renderToStaticMarkup(
    React.createElement(AiUpscalePanelView, {
      enabled: false,
      phase: "idle",
      assess: {
        eligible: true,
        suggestion: true,
        strong: true,
        message: "This artwork is about 120 DPI at the print size, so it may look blurry. Turn on AI Enhance to make it crisp.",
      },
      preview: null,
      slider: 55,
      onToggle: noop,
      onSlider: noop,
      onAccept: noop,
      onKeep: noop,
    }),
  );

  assert.match(html, /AI Enhance \/ Upscale/);
  assert.match(html, new RegExp(AI_UPSCALE_EXPLANATION.replace(/[.]/g, "\\.")));
  assert.match(html, /data-testid="switch-ai-upscale"/);
  assert.match(html, /aria-checked="false"/);
  assert.match(html, /about 120 DPI/);
  assert.equal(html.includes("button-accept-ai-upscale"), false);
  assert.equal(html.includes("ai-upscale-compare"), false);
});

test("ready step shows a before and after slider plus accept or keep original", () => {
  const html = renderToStaticMarkup(
    React.createElement(AiUpscalePanelView, {
      enabled: true,
      phase: "ready",
      assess: { eligible: true, suggestion: true, strong: false, message: "Under 300 DPI." },
      preview: {
        beforeUrl: "/before.png",
        afterUrl: "/after.png",
        basic: true,
        provider: "basic",
        message: "Basic enhancement",
      },
      slider: 40,
      onToggle: noop,
      onSlider: noop,
      onAccept: noop,
      onKeep: noop,
    }),
  );

  assert.match(html, /data-testid="ai-upscale-compare"/);
  assert.match(html, /data-testid="img-ai-upscale-before"/);
  assert.match(html, /data-testid="img-ai-upscale-after"/);
  assert.match(html, /data-testid="slider-ai-upscale"/);
  assert.match(html, /data-testid="button-accept-ai-upscale"/);
  assert.match(html, /data-testid="button-keep-original"/);
  assert.match(html, /Basic enhancement/);
  assert.match(html, /clip-path:inset\(0 60% 0 0\)/);
  assert.match(html, /aria-checked="true"/);
});

test("a failed upscaler tells the user the original will be used", () => {
  const html = renderToStaticMarkup(
    React.createElement(AiUpscalePanelView, {
      enabled: true,
      phase: "fallback",
      assess: { eligible: true },
      preview: {
        provider: "original",
        message: "The AI upscaler is busy right now, so we'll keep your original artwork and continue.",
      },
      slider: 50,
      onToggle: noop,
      onSlider: noop,
      onAccept: noop,
      onKeep: noop,
    }),
  );

  assert.match(html, /data-testid="ai-upscale-fallback"/);
  assert.match(html, /keep your original artwork and continue/);
  assert.equal(html.includes("button-accept-ai-upscale"), false);
});
