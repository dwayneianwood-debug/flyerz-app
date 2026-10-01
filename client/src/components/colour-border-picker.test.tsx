import React from "react";
import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ColourBorderPicker } from "./colour-border-picker";
import { COLOUR_BORDER_PRESETS, cmykToRgb, presetChoice, rgbToCmyk } from "../lib/colour-border";

test("colour border presets include print colours with swatches", () => {
  const html = renderToStaticMarkup(
    <ColourBorderPicker value={presetChoice("cyan")} onChange={() => undefined} />,
  );
  assert.match(html, /data-testid="colour-border-picker"/);
  assert.match(html, /Cyan/);
  assert.match(html, /C100 M0 Y0 K0/);
  assert.match(html, /background-color:#00ffff/i);
});

test("custom colour panel offers a picker and CMYK inputs", () => {
  const html = renderToStaticMarkup(
    <ColourBorderPicker
      value={{ source: "custom", presetId: "custom", label: "Custom", c: 10, m: 20, y: 30, k: 40, r: 138, g: 122, b: 107 }}
      onChange={() => undefined}
    />,
  );
  assert.match(html, /data-testid="panel-colour-custom"/);
  assert.match(html, /data-testid="input-colour-picker"/);
  assert.match(html, /data-testid="input-colour-c"/);
  assert.match(html, /data-testid="input-colour-k"/);
});

test("preset CMYK matches the on-screen RGB equivalent", () => {
  for (const preset of COLOUR_BORDER_PRESETS) {
    const rgb = cmykToRgb(preset.c, preset.m, preset.y, preset.k);
    assert.deepEqual(rgb, { r: preset.r, g: preset.g, b: preset.b });
  }
  const red = rgbToCmyk(255, 0, 0);
  assert.equal(red.m, 100);
  assert.equal(red.y, 100);
  assert.equal(red.k, 0);
});
