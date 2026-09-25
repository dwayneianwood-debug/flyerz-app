import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  ARTWORK_PREVIEW_MAX_HEIGHT_RATIO,
  ARTWORK_PREVIEW_MAX_WIDTH_RATIO,
  ARTWORK_THUMB_FRAME_CLASS,
  ArtworkPreviewFigure,
  ArtworkThumbnail,
  artworkPreviewLabels,
  fitArtworkPreviewSize,
} from "./artwork-thumbnail";

test("enlarged preview scales up and down while keeping aspect ratio", () => {
  const viewport = { width: 1000, height: 800 };
  const maxW = viewport.width * ARTWORK_PREVIEW_MAX_WIDTH_RATIO;
  const maxH = viewport.height * ARTWORK_PREVIEW_MAX_HEIGHT_RATIO;

  const landscape = fitArtworkPreviewSize(2000, 1000, viewport.width, viewport.height);
  assert.equal(landscape.width, Math.round(maxW));
  assert.ok(landscape.height < maxH);
  assert.ok(Math.abs(landscape.width / landscape.height - 2) < 0.02);

  const portrait = fitArtworkPreviewSize(1000, 2000, viewport.width, viewport.height);
  assert.equal(portrait.height, Math.round(maxH));
  assert.ok(portrait.width < maxW);
  assert.ok(Math.abs(portrait.width / portrait.height - 0.5) < 0.02);

  const small = fitArtworkPreviewSize(100, 50, viewport.width, viewport.height);
  assert.ok(small.width > 100 && small.height > 50);
  assert.equal(small.width, Math.round(maxW));
  assert.ok(Math.abs(small.width / small.height - 2) < 0.02);

  const huge = fitArtworkPreviewSize(8000, 6000, viewport.width, viewport.height);
  assert.ok(huge.width <= maxW + 1);
  assert.ok(huge.height <= maxH + 1);
  assert.ok(Math.abs(huge.width / huge.height - 8000 / 6000) < 0.02);
});

test("fitArtworkPreviewSize rejects empty dimensions", () => {
  assert.deepEqual(fitArtworkPreviewSize(0, 100, 1000, 800), { width: 0, height: 0 });
  assert.deepEqual(fitArtworkPreviewSize(100, 0, 1000, 800), { width: 0, height: 0 });
  assert.deepEqual(fitArtworkPreviewSize(100, 100, 0, 800), { width: 0, height: 0 });
});

test("artwork preview labels include filename and 300 DPI dimensions when known", () => {
  assert.deepEqual(artworkPreviewLabels("poster.jpg", { w: 91, h: 114 }), {
    fileName: "poster.jpg",
    dimensions: "91 × 114mm @ 300 DPI",
  });
  assert.equal(artworkPreviewLabels("brief.pdf", null).dimensions, null);
  assert.equal(artworkPreviewLabels("brief.pdf", { w: 0, h: 10 }).dimensions, null);
});

test("image thumbnail is a larger clickable control with a zoom affordance", () => {
  const html = renderToStaticMarkup(
    createElement(ArtworkThumbnail, {
      previewUrl: "blob:artwork",
      fileName: "CRUSADDE MAKHUSHANE CYMK_260804_195245.jpg",
      dimensionsMm: { w: 91, h: 114 },
    }),
  );

  assert.match(html, /data-testid="button-open-artwork-preview"/);
  assert.match(html, /cursor-pointer/);
  assert.match(html, /group-hover:bg-black\/25/);
  assert.match(html, /data-testid="icon-artwork-zoom"/);
  assert.match(html, /data-testid="img-staged-preview"/);
  assert.match(html, /src="blob:artwork"/);
  assert.match(html, /View larger preview of CRUSADDE MAKHUSHANE CYMK_260804_195245\.jpg/);
  assert.match(html, new RegExp(ARTWORK_THUMB_FRAME_CLASS.replace(" ", " ")));
  assert.equal(html.includes("h-16"), false);
  assert.equal(html.includes("w-16"), false);
});

test("thumbnail without a preview image is not clickable", () => {
  const html = renderToStaticMarkup(
    createElement(ArtworkThumbnail, {
      previewUrl: null,
      fileName: "layout.pdf",
    }),
  );

  assert.match(html, /data-testid="artwork-thumb-placeholder"/);
  assert.equal(html.includes("button-open-artwork-preview"), false);
  assert.equal(html.includes("img-staged-preview"), false);
  assert.equal(html.includes("<button"), false);
  assert.match(html, new RegExp(ARTWORK_THUMB_FRAME_CLASS));
});

test("preview figure fits the image and shows filename plus dimensions", () => {
  const html = renderToStaticMarkup(
    createElement(ArtworkPreviewFigure, {
      previewUrl: "/previews/page.png",
      fileName: "layout.pdf",
      dimensionsLabel: "210 × 297mm @ 300 DPI",
      size: { width: 640, height: 480 },
      title: createElement("span", { "data-testid": "text-artwork-preview-filename" }, "layout.pdf"),
      description: createElement(
        "span",
        { "data-testid": "text-artwork-preview-dimensions" },
        "210 × 297mm @ 300 DPI",
      ),
    }),
  );

  assert.match(html, /data-testid="img-artwork-preview-large"/);
  assert.match(html, /src="\/previews\/page\.png"/);
  assert.match(html, /object-contain/);
  assert.match(html, /max-width:92vw/);
  assert.match(html, /max-height:78vh/);
  assert.match(html, /width:640px/);
  assert.match(html, /height:480px/);
  assert.match(html, /data-testid="text-artwork-preview-filename"/);
  assert.match(html, /layout\.pdf/);
  assert.match(html, /210 × 297mm @ 300 DPI/);
});

test("preview figure omits the dimensions line when they are unknown", () => {
  const html = renderToStaticMarkup(
    createElement(ArtworkPreviewFigure, {
      previewUrl: "/previews/page.png",
      fileName: "notes.docx",
      dimensionsLabel: null,
      size: null,
      title: createElement("span", null, "notes.docx"),
      description: createElement("span", { className: "sr-only" }, "Full-size artwork preview"),
    }),
  );

  assert.match(html, /notes\.docx/);
  assert.match(html, /Full-size artwork preview/);
  assert.equal(html.includes("mm @ 300 DPI"), false);
});
