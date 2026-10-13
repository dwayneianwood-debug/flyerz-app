import React from "react";
import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { BleedMethodSelector } from "./bleed-method-selector";

const border = {
  source: "preset" as const,
  presetId: "white",
  label: "White",
  c: 0,
  m: 0,
  y: 0,
  k: 0,
  r: 255,
  g: 255,
  b: 255,
};

test("automatic bleed names the method on each edge", () => {
  const html = renderToStaticMarkup(
    <BleedMethodSelector
      jobId={1}
      variants={{}}
      recommended="auto"
      selected="auto"
      onSelect={() => undefined}
      loading={false}
      colourBorder={border}
      onColourBorderChange={() => undefined}
      pressEngine={{
        passed: true,
        status: "ready",
        headline: "Ready for press",
        bleedNote: "Bleed: top/bottom flat colour, left/right mirrored photo",
        edges: [
          { side: "top", note: "Top: continued the cleaned edge colour." },
          { side: "left", note: "Left: mirrored the cleaned edge." },
        ],
      }}
    />,
  );
  assert.match(html, /data-testid="text-press-ready-bleed-note"/);
  assert.match(html, /Bleed: top\/bottom flat colour, left\/right mirrored photo/);
});
