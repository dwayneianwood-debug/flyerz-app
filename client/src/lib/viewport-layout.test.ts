import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  anchoredBottom,
  chatPanelInside,
  chatPanelLayout,
  speechBubbleMaxWidth,
} from "./viewport-layout";

const PHONE = [320, 360, 390, 430, 768];
const DESKTOP = [1024, 1280, 1440];

test("the chat panel fits a phone and a desktop in every browser", () => {
  for (const width of [...PHONE, ...DESKTOP]) {
    assert.equal(chatPanelInside(width), true, `width ${width}`);
  }
  assert.deepEqual(chatPanelLayout(390), { width: 240, marginLeft: 0 });
  assert.deepEqual(chatPanelLayout(320), { width: 240, marginLeft: 0 });
  assert.equal(chatPanelLayout(320).width, 240);
  assert.equal(speechBubbleMaxWidth(320), 220);
  assert.equal(speechBubbleMaxWidth(390), 220);
  assert.deepEqual(chatPanelLayout(1280), { width: 330, marginLeft: -105 });
  assert.equal(speechBubbleMaxWidth(1280), 240);
});

test("a narrow phone still keeps the panel inside the gutter", () => {
  assert.equal(chatPanelLayout(280).width, 240);
  assert.equal(chatPanelInside(280), true);
  assert.equal(speechBubbleMaxWidth(280), 220);
});

test("the cat stays on the bottom and rises only for the safe area", () => {
  assert.equal(anchoredBottom(12, 0), 12);
  assert.equal(anchoredBottom(12, 34), 46);
  assert.equal(anchoredBottom(12, -8), 12);
});

test("the page font and the cat panel do not depend on a system face", () => {
  const here = new URL(".", import.meta.url);
  const css = readFileSync(new URL("../index.css", here), "utf8");
  const html = readFileSync(new URL("../../index.html", here), "utf8");
  const widget = readFileSync(new URL("../components/glitchy-widget.tsx", here), "utf8");
  assert.equal(css.includes("fonts.googleapis.com"), false);
  assert.equal(html.includes("fonts.googleapis.com"), false);
  assert.equal(css.includes("system-ui"), false);
  assert.equal(css.includes("-apple-system"), false);
  assert.match(css, /LiberationSans-Regular\.ttf/);
  assert.match(css, /LiberationSans-Bold\.ttf/);
  assert.match(css, /100dvh/);
  assert.match(widget, /chatPanelLayout/);
  assert.match(widget, /env\(safe-area-inset-bottom, 0px\)/);
  assert.match(widget, /placement\.collapsed \? 44 : 120/);
});
