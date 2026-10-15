import React from "react";
import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { FlowTabs, PrintFlowView, type IntakeItem } from "./print-flow-view";
import { ManualWizard } from "./manual-wizard";

const noop = () => undefined;
const items: IntakeItem[] = [
  { id: "e5", num: "E5", name: "Minimum text size", detail: "We strongly recommend larger text.", severity: "amber" },
  { id: "dpi", num: "4", name: "Resolution", detail: "A picture is under 300 ppi.", severity: "red" },
  { id: "cmyk", num: "2", name: "CMYK", detail: "The file is CMYK.", severity: "green" },
];

const base = {
  productId: "a5",
  customW: "",
  customH: "",
  fileLabel: "flyer.pdf",
  busy: false,
  items,
  acknowledged: {},
  replicate: false,
  cat: true,
  catText: "",
  upscale: false,
  fixes: [
    { id: "checking", label: "Checking the file", percent: 100 },
    { id: "press", label: "Building the press PDF", percent: 78 },
  ],
  card: null,
  bleedOpen: false,
  bleedBusy: false,
  copied: false,
  error: "",
  onProduct: noop,
  onCustomW: noop,
  onCustomH: noop,
  onFiles: noop,
  onCheck: noop,
  onAck: noop,
  onReplicate: noop,
  onCat: noop,
  onCatText: noop,
  onUpscale: noop,
  onMake: noop,
  onBleedOpen: noop,
  onBleed: noop,
  onCopy: noop,
};

test("quick check stops on red and offers the options beside it", () => {
  const html = renderToStaticMarkup(<PrintFlowView {...base} stage="check" />);
  assert.match(html, /data-testid="check-row-e5"/);
  assert.match(html, /Strong recommendation/);
  assert.match(html, /data-testid="ack-dpi"/);
  assert.match(html, /data-testid="panel-pre-fix"/);
  assert.match(html, /AI enlarge \(pictures only\)/);
  assert.match(html, /Small time, date, or number edit/);
  assert.match(html, /Upscale with AI/);
  assert.match(html, /Make file print ready/);
  assert.match(html, /disabled/);
  const ticked = renderToStaticMarkup(
    <PrintFlowView {...base} stage="check" acknowledged={{ dpi: true }} />,
  );
  assert.match(ticked, /Accepted, now a recommendation/);
  assert.equal(ticked.includes("button-make-print-ready\" disabled"), false);
});

test("progress lists a percent for each fix", () => {
  const html = renderToStaticMarkup(<PrintFlowView {...base} stage="progress" />);
  assert.match(html, /data-testid="list-fix-progress"/);
  assert.match(html, /Checking the file/);
  assert.match(html, /78%/);
});

test("the result shows before, after, the cutter, the bleed menu, and the customer note", () => {
  const html = renderToStaticMarkup(
    <PrintFlowView
      {...base}
      stage="result"
      bleedOpen
      card={{
        id: 7,
        filename: "flyer.pdf",
        status: "complete",
        light: "amber",
        reasons: [],
        decisions: ["Bleed: top/bottom flat colour, left/right mirrored photo"],
        clientMessage: "",
        customerMessage: "Your artwork has successfully been made print ready, but to ensure you are 100% happy we would recommend checking the points below. Added 5 mm bleed.",
        bleedNote: "Bleed: top/bottom flat colour, left/right mirrored photo",
        approved: false,
        hasPress: true,
        hasProof: true,
        hasBefore: true,
        productLabel: "A5",
        quantity: null,
        notes: "",
      }}
    />,
  );
  assert.match(html, /data-testid="img-before"/);
  assert.match(html, /data-testid="img-after"/);
  assert.match(html, /data-testid="cutter-preview"/);
  assert.match(html, /pink line is the trim/);
  assert.match(html, /Proceed to download print-ready artwork/);
  assert.match(html, /data-testid="menu-change-bleed"/);
  assert.match(html, /data-testid="bleed-method-mirror"/);
  assert.match(html, /data-testid="text-customer-message"/);
  assert.match(html, /100% happy/);
  assert.match(html, /data-testid="button-copy-customer"/);
  assert.match(html, /Bleed: top\/bottom flat colour/);
});

test("the manual tab keeps the upload wizard", () => {
  const html = renderToStaticMarkup(
    <div>
      <FlowTabs active="manual" onReady={noop} onManual={noop} />
      <ManualWizard><p>Upload form</p></ManualWizard>
    </div>,
  );
  assert.match(html, /data-testid="tab-manual"/);
  assert.match(html, /Fix my artwork manually/);
  assert.match(html, /Upload/);
  assert.match(html, /Size/);
  assert.match(html, /Crop &amp; Submit|Crop & Submit/);
});

test("when Replicate is switched off both AI ticks are greyed out with the reason", () => {
  const off = renderToStaticMarkup(<PrintFlowView {...base} stage="check" replicate upscale replicateOff />);
  assert.match(off, /Replicate is switched off in settings/);
  assert.match(off, /disabled=""[^>]*data-testid="option-upscale"|data-testid="option-upscale"[^>]*disabled=""/);
  assert.match(off, /disabled=""[^>]*data-testid="option-replicate"|data-testid="option-replicate"[^>]*disabled=""/);
  const on = renderToStaticMarkup(<PrintFlowView {...base} stage="check" />);
  assert.equal(on.includes("Replicate is switched off in settings"), false);
});
