import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "http";
import os from "os";
import path from "path";
import express from "express";
import { PRESS_FILE_MISSING, firstExistingPressFile, pressFileCandidates } from "./pressDownload.ts";

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

test("Windows press paths resolve, and the database PDF wins", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-press-win-"));
  const quick = path.join(root, "uploads", "quick-print", "1267", "press.pdf");
  const big = path.join(root, "uploads", "processed_1268_banner.pdf");
  const small = path.join(root, "uploads", "quick-print", "1268", "press.pdf");
  fs.mkdirSync(path.dirname(quick), { recursive: true });
  fs.mkdirSync(path.dirname(small), { recursive: true });
  fs.writeFileSync(quick, "%PDF-1.4 job-1267-press\n");
  fs.writeFileSync(big, "%PDF-1.4 BIG-DATABASE-PRESS\n");
  fs.writeFileSync(small, "%PDF-1.4 small-compiled\n");
  const exists = (file: string) => fs.existsSync(file);

  const job1267 = {
    id: 1267,
    correctedPath: "",
    auditResults: { quickPrint: { pressPath: "uploads\\quick-print\\1267\\press.pdf" } },
  };
  const found1267 = firstExistingPressFile(job1267, () => true, exists, root);
  assert.equal(found1267, quick);
  assert.match(fs.readFileSync(found1267 || "", "utf8"), /job-1267-press/);

  const storedOnly = {
    id: 1267,
    correctedPath: "uploads\\quick-print\\1267\\press.pdf",
    auditResults: {},
  };
  assert.equal(firstExistingPressFile(storedOnly, () => true, exists, root), quick);

  const job1268 = {
    id: 1268,
    correctedPath: "uploads\\processed_1268_banner.pdf",
    auditResults: {
      compiledPdfPath: "uploads\\quick-print\\1268\\press.pdf",
      quickPrint: { pressPath: "uploads\\quick-print\\1268\\press.pdf" },
    },
  };
  const found1268 = firstExistingPressFile(job1268, () => true, exists, root);
  assert.equal(found1268, big);
  assert.match(fs.readFileSync(found1268 || "", "utf8"), /BIG-DATABASE-PRESS/);
  assert.equal(fs.readFileSync(found1268 || "", "utf8").includes("small-compiled"), false);

  const drive = {
    id: 1268,
    correctedPath: "C:\\Users\\print\\flyerz\\uploads\\processed_1268_banner.pdf",
    auditResults: { compiledPdfPath: "uploads\\quick-print\\1268\\press.pdf" },
  };
  assert.equal(firstExistingPressFile(drive, () => true, exists, root), big);

  const missing = { id: 9999, correctedPath: "uploads\\missing.pdf", auditResults: { checks: [] } };
  assert.equal(firstExistingPressFile(missing, () => true, exists, root), null);
  assert.deepEqual(pressFileCandidates({ correctedPath: "/tmp/preview.png", auditResults: {} }, root), []);

  const app = express();
  app.get("/api/jobs/:id/download/press-ready", (req, res) => {
    const job = req.params.id === "1268" ? job1268 : missing;
    const file = firstExistingPressFile(job, () => true, exists, root);
    if (!file) return res.status(404).json({ message: PRESS_FILE_MISSING });
    return res.download(file, "Print Ready Artwork.pdf");
  });
  const server = http.createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  try {
    const press = await fetch(`http://127.0.0.1:${port}/api/jobs/1268/download/press-ready`);
    assert.equal(press.status, 200);
    assert.match(Buffer.from(await press.arrayBuffer()).toString(), /BIG-DATABASE-PRESS/);
    const absent = await fetch(`http://127.0.0.1:${port}/api/jobs/9999/download/press-ready`);
    assert.equal(absent.status, 404);
    const body = await absent.json() as { message?: string };
    assert.equal(body.message, PRESS_FILE_MISSING);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
