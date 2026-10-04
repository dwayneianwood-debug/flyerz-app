import React from "react";
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { BleedSizeControl } from "@/components/bleed-size-control";
import { BLEED_PRESETS_MM } from "@shared/bleed-size";
import { BLEED_STRATEGY_IDS } from "@shared/schema";
import { BleedMethodSelector } from "@/components/bleed-method-selector";

const colour = {
  source: "preset" as const,
  presetId: "black",
  label: "Black",
  c: 0,
  m: 0,
  y: 0,
  k: 100,
  r: 0,
  g: 0,
  b: 0,
};

test("bleed sizes and every style are on the page, including later pages", () => {
  const sizes = renderToStaticMarkup(
    <BleedSizeControl value={5} onChange={() => undefined} />,
  );
  for (const mm of BLEED_PRESETS_MM) {
    assert.match(sizes, new RegExp(`data-testid="button-bleed-${mm}"`));
  }
  assert.match(sizes, /data-testid="input-bleed-custom"/);
  assert.deepEqual([...BLEED_PRESETS_MM], [3, 5, 10]);

  const pages = ["/a.png", "/b.png", "/c.png"];
  const variants = Object.fromEntries(BLEED_STRATEGY_IDS.map((id) => [id, pages[0]]));
  const styles = renderToStaticMarkup(
    <BleedMethodSelector
      jobId={7}
      variants={variants}
      recommended="mirror"
      selected="mirror"
      onSelect={() => undefined}
      loading={false}
      colourBorder={colour}
      onColourBorderChange={() => undefined}
      beforeUrl={null}
      afterUrl={null}
      proofPage={2}
      onProofPage={() => undefined}
      variantPages={{ mirror: pages }}
    />,
  );
  for (const id of BLEED_STRATEGY_IDS) {
    assert.match(styles, new RegExp(`option-bleed-method-${id}`));
  }
  assert.match(styles, /Gradient Extrapolate/);
  assert.match(styles, /Frequency Separated/);
  assert.match(styles, /data-testid="style-page-selector"/);
  assert.match(styles, /data-testid="button-style-page-1"/);
  assert.match(styles, /data-testid="button-style-page-2"/);
  assert.match(styles, /data-testid="button-style-page-3"/);
  assert.match(styles, /bleed-variant\/mirror\?page=2/);
  assert.doesNotMatch(styles, /\(manual\)/);

  const proof = renderToStaticMarkup(
    <BleedMethodSelector
      jobId={7}
      variants={variants}
      recommended="mirror"
      selected="auto"
      onSelect={() => undefined}
      loading={false}
      colourBorder={colour}
      onColourBorderChange={() => undefined}
      beforeUrl="/api/jobs/7/proof?page=2"
      afterUrl="/api/jobs/7/bleed-preview/page-3.png"
      proofPage={2}
      onProofPage={() => undefined}
      variantPages={{ mirror: pages }}
    />,
  );
  assert.match(proof, /img-press-before/);
  assert.match(proof, /\/api\/jobs\/7\/proof\?page=2/);
  assert.match(proof, /img-press-after/);
  assert.match(proof, /bleed-preview\/page-3\.png/);

  const source = fs.readFileSync(new URL("./job-details.tsx", import.meta.url), "utf8");
  assert.match(source, /data-testid="advanced-bleed-size"/);
  assert.doesNotMatch(source, /<details[\s\S]{0,400}advanced-bleed-size/);
  assert.doesNotMatch(source, /\(manual\)/);
});
