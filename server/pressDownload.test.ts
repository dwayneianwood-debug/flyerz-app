import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "http";
import os from "os";
import path from "path";
import express from "express";
import { firstExistingPressFile, pressFileCandidates } from "./pressDownload.ts";

test("quick-mode press download does not need compiledPdfPath", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-press-dl-"));
  const pdf = path.join(dir, "press.pdf");
  fs.writeFileSync(pdf, "%PDF-1.4 quick press\n");
  const job = {
    filename: "banner.png",
    correctedPath: pdf,
    auditResults: {
      quickPrint: { pressPath: pdf, light: "amber" as const },
    },
  };
  assert.deepEqual(pressFileCandidates(job), [pdf]);
  assert.equal(firstExistingPressFile(job, () => true, (file) => fs.existsSync(file)), pdf);

  const missingCompiled = {
    correctedPath: pdf,
    auditResults: { compiledPdfPath: "", quickPrint: { pressPath: pdf } },
  };
  assert.equal(pressFileCandidates(missingCompiled)[0], pdf);

  const app = express();
  const send = (res: express.Response) => {
    const file = firstExistingPressFile(job, () => true, (item) => fs.existsSync(item));
    if (!file) return res.status(404).json({ message: "No compiled PDF available" });
    return res.download(file, "Print Ready Artwork.pdf");
  };
  app.get("/api/jobs/:id/download/press-ready", (_req, res) => send(res));
  app.get("/api/jobs/:id/download/corrected", (_req, res) => send(res));
  const server = http.createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  try {
    const press = await fetch(`http://127.0.0.1:${port}/api/jobs/214/download/press-ready`);
    assert.equal(press.status, 200);
    const bytes = Buffer.from(await press.arrayBuffer());
    assert.equal(bytes.subarray(0, 5).toString(), "%PDF-");
    const corrected = await fetch(`http://127.0.0.1:${port}/api/jobs/214/download/corrected`);
    assert.equal(corrected.status, 200);
    assert.equal(Buffer.from(await corrected.arrayBuffer()).subarray(0, 5).toString(), "%PDF-");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("an ordinary job without a compiled PDF is not served from a random corrected file", () => {
  const job = {
    correctedPath: "/tmp/preview.png",
    auditResults: { checks: [] },
  };
  assert.deepEqual(pressFileCandidates(job), []);
});
