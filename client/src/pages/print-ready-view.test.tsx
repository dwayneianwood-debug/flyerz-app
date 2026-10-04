import React from "react";
import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { QUICK_PRINT_PRODUCTS } from "@shared/quickPrint";
import { QuickPrintForm, QuickResultCard, WatcherSettings } from "./print-ready-view";

const noop = () => undefined;

test("quick print form lists the product sizes and one button", () => {
  const html = renderToStaticMarkup(
    <QuickPrintForm
      productId="a5"
      customW=""
      customH=""
      quantity=""
      notes=""
      fileLabel=""
      busy={false}
      onProduct={noop}
      onCustomW={noop}
      onCustomH={noop}
      onQuantity={noop}
      onNotes={noop}
      onFiles={noop}
      onSubmit={noop}
    />,
  );
  assert.match(html, /Make it print-ready/);
  assert.match(html, /data-testid="button-make-print-ready"/);
  assert.match(html, /A6 landscape \(148 × 105 mm\)/);
  assert.match(html, /A5 \(148 × 210 mm\)/);
  assert.match(html, /DL \(99 × 210 mm\)/);
  assert.match(html, /Business card 90 × 50/);
  assert.match(html, /Business card 90 × 55/);
  assert.match(html, /Custom size/);
  assert.equal(QUICK_PRINT_PRODUCTS.some((product) => product.id === "a5" && product.widthMm === 148), true);
  assert.equal(QUICK_PRINT_PRODUCTS.some((product) => product.id === "a6-landscape" && product.widthMm === 148 && product.heightMm === 105), true);
});

test("result cards show green download, amber reasons, and a red client message", () => {
  const green = renderToStaticMarkup(
    <QuickResultCard
      card={{
        id: 1,
        filename: "flyer.png",
        status: "complete",
        light: "green",
        reasons: [],
        decisions: ["Bleed is 5 mm on every side."],
        checklist: [
          { id: "bleed", label: "5 mm bleed is present and filled, with no slivers", passed: true, detail: "" },
          { id: "fonts", label: "Fonts are embedded", passed: false, detail: "A font in the press file is not embedded." },
        ],
        clientMessage: "",
        approved: false,
        hasPress: true,
        hasProof: true,
        productLabel: "A5",
        quantity: null,
        notes: "",
      }}
    />,
  );
  assert.match(green, /GREEN/);
  assert.match(green, /check-bleed-1/);
  assert.match(green, />Pass</);
  assert.match(green, /5 mm bleed is present and filled, with no slivers/);
  assert.match(green, />Fail</);
  assert.match(green, /Fonts are embedded/);
  assert.match(green, /Download press PDF/);
  assert.match(green, /\/api\/jobs\/1\/download\/press-ready/);
  assert.match(green, /img-proof-1/);

  const amber = renderToStaticMarkup(
    <QuickResultCard
      card={{
        id: 2,
        filename: "wide.png",
        status: "complete",
        light: "amber",
        reasons: ["The picture was a different shape, so the edges were extended. Glance at those edges."],
        decisions: ["Bleed is 5 mm on every side."],
        clientMessage: "",
        approved: false,
        hasPress: true,
        hasProof: true,
        productLabel: "A5",
        quantity: 100,
        notes: "",
      }}
    />,
  );
  assert.match(amber, /AMBER/);
  assert.match(amber, /edges were extended/);
  assert.match(amber, /data-testid="button-approve-2"/);

  const red = renderToStaticMarkup(
    <QuickResultCard
      card={{
        id: 3,
        filename: "brief.docx",
        status: "complete",
        light: "red",
        reasons: ["Word and PowerPoint files cannot be printed as they are."],
        decisions: [],
        clientMessage: "Please send a PDF.",
        approved: false,
        hasPress: false,
        hasProof: false,
        productLabel: "A5",
        quantity: null,
        notes: "",
      }}
    />,
  );
  assert.match(red, /RED/);
  assert.match(red, /Please send a PDF/);
  assert.match(red, /Copy message for the client/);
  assert.match(red, /Nothing is sent until you copy this and send it yourself/);
  assert.equal(red.includes("Download press PDF"), false);
});

test("a job that is still running shows the real stage", () => {
  const html = renderToStaticMarkup(
    <QuickResultCard
      card={{
        id: 9,
        filename: "leaflet.png",
        status: "processing",
        light: null,
        reasons: [],
        decisions: [],
        clientMessage: "",
        approved: false,
        hasPress: false,
        hasProof: false,
        productLabel: "A5",
        quantity: null,
        notes: "",
        stage: "Enlarging the artwork",
        elapsedSec: 12,
        note: "Original lettering is kept. Text is not retyped.",
      }}
    />,
  );
  assert.match(html, /Enlarging the artwork/);
  assert.match(html, /12s/);
  assert.match(html, /not retyped/);
  assert.equal(html.includes("95%"), false);
});

test("drop folder control is off unless the box is ticked", () => {
  const html = renderToStaticMarkup(
    <WatcherSettings
      enabled={false}
      folderPath=""
      defaultProductId="a5"
      onEnabled={noop}
      onFolder={noop}
      onProduct={noop}
      onSave={noop}
    />,
  );
  assert.match(html, /off by default/);
  assert.match(html, /does not need administrator rights/);
  assert.match(html, /data-testid="watcher-enabled"/);
  assert.equal(html.includes("checked"), false);
});
