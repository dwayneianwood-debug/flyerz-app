import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

test("auto is passed through so Python detects the page size", async () => {
  const { productIdForEngine, resolveQuickProduct } = await import("./quickPrintRunner.ts");
  const fallback = resolveQuickProduct("auto");
  assert.equal(fallback.id, "a5");
  assert.equal(productIdForEngine("auto", fallback.id), "auto");
  assert.equal(productIdForEngine("a6-landscape", "a6-landscape"), "a6-landscape");
});

test("tagged python lines reach the press log", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-log-"));
  process.env.FLYERZ_DB_PATH = path.join(dir, "jobs.sqlite");
  const { pressLogLines } = await import("./quickPrintRunner.ts");
  const lines = pressLogLines(
    "[AI-UPSCALE] replicate status=failed error=denied\n" +
      "  FAIL raster ssim=1.000 'body'\n" +
      "[vector-trace] Timing: trace 1.20s.\n" +
      "plain note\n",
  );
  assert.deepEqual(lines, [
    "[AI-UPSCALE] replicate status=failed error=denied",
    "[vector-trace] Timing: trace 1.20s.",
  ]);
});

test("quick print settings, attention queue, and drop folder", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-quick-"));
  process.env.FLYERZ_DB_PATH = path.join(dir, "jobs.sqlite");
  process.env.QUICK_PRINT_SETTINGS_PATH = path.join(dir, "settings.json");

  const settingsMod = await import("./quickPrintSettings.ts");
  const first = settingsMod.readQuickPrintSettings();
  assert.equal(first.enabled, false);
  assert.equal(first.folderPath, "");
  assert.throws(() => settingsMod.writeQuickPrintSettings({ enabled: true, folderPath: "" }));
  const savedOff = settingsMod.writeQuickPrintSettings({ enabled: false, folderPath: "" });
  assert.equal(savedOff.enabled, false);

  const { storage } = await import("./storage.ts");
  const base = {
    checks: [],
    fixesApplied: 0,
    complianceReport: "",
    pressEngine: { passed: true, status: "ready" as const, headline: "Ready for press" },
  };
  const green = await storage.createJob({
    filename: "ready.pdf",
    originalPath: path.join(dir, "ready.pdf"),
    fileSize: 20,
    fileType: "pdf",
  });
  await storage.updateJob(green.id, {
    status: "complete",
    auditResults: { ...base, overallPassed: true, quickPrint: { light: "green", approved: false, pressPath: "" } },
  });
  const amber = await storage.createJob({
    filename: "glance.png",
    originalPath: path.join(dir, "glance.png"),
    fileSize: 20,
    fileType: "png",
  });
  await storage.updateJob(amber.id, {
    status: "complete",
    auditResults: { ...base, overallPassed: false, quickPrint: { light: "amber", approved: false, reasons: ["edges"] } },
  });
  const approved = await storage.createJob({
    filename: "signed.png",
    originalPath: path.join(dir, "signed.png"),
    fileSize: 20,
    fileType: "png",
  });
  await storage.updateJob(approved.id, {
    status: "complete",
    auditResults: { ...base, overallPassed: true, quickPrint: { light: "amber", approved: true } },
  });
  const red = await storage.createJob({
    filename: "brief.docx",
    originalPath: path.join(dir, "brief.docx"),
    fileSize: 20,
    fileType: "docx",
  });
  await storage.updateJob(red.id, {
    status: "complete",
    auditResults: {
      ...base,
      overallPassed: false,
      pressEngine: { passed: false, status: "needs-attention" },
      quickPrint: { light: "red", approved: false, clientMessage: "Please send a PDF." },
    },
  });

  const attention = await storage.listJobs({ limit: 10, offset: 0, attention: true });
  const names = attention.jobs.map((job) => job.filename).sort();
  assert.deepEqual(names, ["brief.docx", "glance.png"]);
  const redRow = attention.jobs.find((job) => job.filename === "brief.docx");
  assert.equal(redRow?.quickLight, "red");
  assert.equal(attention.jobs.some((job) => job.filename === "ready.pdf"), false);
  assert.equal(attention.jobs.some((job) => job.filename === "signed.png"), false);

  const folder = path.join(dir, "drop");
  const input = path.join(folder, "Input");
  fs.mkdirSync(input, { recursive: true });
  const dropped = path.join(input, "from-sales.docx");
  fs.writeFileSync(dropped, "Word file from a client, not a press PDF.");
  const watcher = await import("./quickPrintWatcher.ts");
  settingsMod.writeQuickPrintSettings({ enabled: false, folderPath: folder, defaultProductId: "a5" });
  const skipped = await watcher.scanDropFolderOnce({ force: true });
  assert.equal(skipped.processed.length, 0);
  assert.equal(fs.existsSync(dropped), true);

  settingsMod.writeQuickPrintSettings({ enabled: true, folderPath: folder, defaultProductId: "a5" });
  const ran = await watcher.scanDropFolderOnce({ force: true });
  assert.equal(ran.processed.length, 1);
  assert.match(ran.processed[0], /Needs-attention/);
  assert.equal(fs.existsSync(dropped), false);
  assert.equal(fs.existsSync(path.join(ran.processed[0], "from-sales.docx")), true);
  assert.equal(fs.existsSync(path.join(ran.processed[0], "press.pdf")), false);
  const queued = await storage.listJobs({ limit: 20, offset: 0, attention: true });
  assert.equal(queued.jobs.some((job) => job.filename === "from-sales.docx" && job.quickLight === "red"), true);
});
