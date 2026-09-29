import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AiRebuildPanelView } from "./ai-rebuild-panel";

test("AI rebuild badge is on by default and shows the words that were read", () => {
  const html = renderToStaticMarkup(
    React.createElement(AiRebuildPanelView, {
      plan: {
        detected: true,
        skipped: false,
        recommendation: "This looks like AI-generated artwork. Rebuild is on.",
        reasons: ["1024×1024 pixels matches a common AI image size"],
        ocrText: "SALE",
        blocks: [{ id: "t1", text: "SALE" }],
        steps: [{ name: "OCR", engine: "local", note: "OCR used RapidOCR on this computer." }],
        beforeUrl: "/before.png",
        afterUrl: "/after.png",
      },
      onToggle: () => {},
      onText: () => {},
      onSave: () => {},
    }),
  );
  assert.match(html, /AI artwork — rebuild recommended/);
  assert.match(html, /Rebuild automatically/);
  assert.match(html, /value="SALE"/);
  assert.match(html, /Text found: SALE/);
  assert.match(html, /on this computer/);
  assert.match(html, /before\.png/);
  assert.match(html, /after\.png/);
  assert.match(html, /aria-checked="true"/);
});
