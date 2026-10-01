import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { centeredRatioCrop, croppedDownloadName, pxToMm } from "../client/src/lib/pure-crop-math";

test("pure crop download names keep the original stem", () => {
  assert.equal(croppedDownloadName("CRUSADDE MAKHUSHANE.jpg", "pdf"), "CRUSADDE MAKHUSHANE_cropped.pdf");
  assert.equal(croppedDownloadName("folder/art.png", "jpg"), "art_cropped.jpg");
  assert.equal(pxToMm(300, 300), 25.4);
});

test("ratio crop stays inside the artwork and keeps the proportion", () => {
  const box = centeredRatioCrop(1000, 500, 210 / 297);
  assert.ok(box.x >= 0 && box.y >= 0);
  assert.ok(box.x + box.w <= 1000.01);
  assert.ok(box.y + box.h <= 500.01);
  assert.ok(Math.abs(box.w / box.h - 210 / 297) < 0.001);
});

test("pure crop quality checks", () => {
  const script = path.join(process.cwd(), "server", "pure_crop_check.py");
  const result = spawnSync("python3", [script], { encoding: "utf8" });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

test("upload wizard crop path is unchanged", () => {
  const upload = fs.readFileSync(path.join(process.cwd(), "client/src/components/file-upload.tsx"), "utf8");
  const wizard = fs.readFileSync(path.join(process.cwd(), "client/src/pages/manual-crop.tsx"), "utf8");
  const pipeline = fs.readFileSync(path.join(process.cwd(), "server/manual_crop.py"), "utf8");
  assert.match(upload, /ManualCropEmbedded/);
  assert.match(wizard, /export function ManualCropEmbedded/);
  assert.match(pipeline, /def _crop_pdf/);
  assert.match(pipeline, /get_pixmap/);
});
