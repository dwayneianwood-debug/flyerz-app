import { readReplicateSettings, writeReplicateSettings } from "./replicateSettings";
import type { Express } from "express";
import fs from "fs";
import path from "path";
import { diskUpload } from "./uploadLimit";
import { isAllowedUpload, INVALID_UPLOAD_MESSAGE } from "./illustratorIntake";
import { storage } from "./storage";
import { readQuickPrintSettings, writeQuickPrintSettings } from "./quickPrintSettings";
import {
  fileTypeForName,
  jobOutputDir,
  productIdForEngine,
  redFallback,
  resolveQuickProduct,
  runBleedReapply,
  runIntakeCheck,
  runQuickPrintFile,
  saveQuickResult,
} from "./quickPrintRunner";
import { beginJobRun, endJobRun } from "./jobRunLock";
import { readJobProgress, writeJobProgress } from "./jobProgress";
import { startQuickPrintWatcher } from "./quickPrintWatcher";
import { BLEED_STRATEGY_IDS, type AuditResults } from "@shared/schema";
import type { QuickPrintCard } from "@shared/quickPrint";

const uploadDir = path.join(process.cwd(), "uploads");
const upload = diskUpload(uploadDir, (_req, file, cb) => {
  if (isAllowedUpload(file.originalname, file.mimetype)) cb(null, true);
  else cb(new Error(INVALID_UPLOAD_MESSAGE));
});

function safeUploadName(original: string): string {
  const base = path.basename(original || "artwork").replace(/[^\w. -]+/g, "_");
  return base.slice(0, 120) || "artwork";
}

function insideWorkspace(filePath: string): boolean {
  const resolved = path.resolve(filePath);
  const root = path.resolve(process.cwd());
  return resolved.startsWith(root + path.sep) || resolved === root;
}

export function slimQuickCard(job: {
  id: number;
  filename: string;
  status: string;
  auditResults?: {
    quickPrint?: {
      light?: string;
      reasons?: string[];
      decisions?: string[];
      checklist?: { id?: string; label?: string; passed?: boolean; detail?: string }[];
      clientMessage?: string;
      approved?: boolean;
      pressPath?: string;
      proofPng?: string;
      productLabel?: string;
      quantity?: number | null;
      notes?: string;
      bleedNote?: string;
      customerMessage?: string;
      beforePng?: string;
      trimWidthMm?: number | null;
      trimHeightMm?: number | null;
      bleedRevision?: number;
    };
  } | null;
}): QuickPrintCard {
  const quick = job.auditResults?.quickPrint;
  const engineNote = (job.auditResults as { pressEngine?: { bleedNote?: string } } | null | undefined)?.pressEngine?.bleedNote;
  const light = quick?.light === "green" || quick?.light === "amber" || quick?.light === "red" ? quick.light : null;
  return {
    id: job.id,
    filename: job.filename,
    status: job.status,
    light,
    reasons: Array.isArray(quick?.reasons) ? quick.reasons.map(String) : [],
    decisions: Array.isArray(quick?.decisions) ? quick.decisions.map(String) : [],
    checklist: Array.isArray(quick?.checklist)
      ? quick.checklist.map((item) => ({
          id: String(item?.id || ""),
          label: String(item?.label || ""),
          passed: item?.passed === true,
          detail: String(item?.detail || ""),
        }))
      : [],
    clientMessage: String(quick?.clientMessage || ""),
    approved: quick?.approved === true,
    hasPress: !!(quick?.pressPath && fs.existsSync(quick.pressPath)),
    hasProof: !!(quick?.proofPng && fs.existsSync(quick.proofPng)),
    productLabel: String(quick?.productLabel || ""),
    quantity: quick?.quantity == null ? null : Number(quick.quantity),
    notes: String(quick?.notes || ""),
    bleedNote: String(quick?.bleedNote || engineNote || ""),
    customerMessage: String(quick?.customerMessage || ""),
    hasBefore: !!(quick?.beforePng && fs.existsSync(quick.beforePng)),
    trimWidthMm: quick?.trimWidthMm == null ? null : Number(quick.trimWidthMm),
    trimHeightMm: quick?.trimHeightMm == null ? null : Number(quick.trimHeightMm),
    bleedRevision: Number(quick?.bleedRevision) || 0,
    ...progressFields(job.id),
  };
}

function progressFields(jobId: number): { stage?: string; percent?: number; elapsedSec?: number; note?: string; fixes?: { id: string; label: string; percent: number }[] } {
  const progress = readJobProgress(jobId);
  if (!progress) return {};
  return {
    stage: progress.stage,
    percent: progress.percent,
    elapsedSec: progress.elapsedSec,
    note: progress.note,
    fixes: progress.fixes || [],
  };
}

async function runJob(jobId: number, inputPath: string, filename: string, body: Record<string, unknown>) {
  const requestedId = String(body.productId || "a5");
  const product = resolveQuickProduct(requestedId, Number(body.customWidth), Number(body.customHeight));
  const quantity = Number(body.quantity);
  const notes = String(body.notes || "").slice(0, 400);
  try {
    const result = await runQuickPrintFile(inputPath, jobOutputDir(jobId), {
      productId: productIdForEngine(requestedId, product.id),
      trimW: product.widthMm,
      trimH: product.heightMm,
      quantity: Number.isFinite(quantity) && quantity > 0 ? quantity : null,
      notes,
      filename,
      jobId,
      editRequest: String(body.editRequest || ""),
      replicateEdit: body.replicateEdit === true || body.replicateEdit === "1",
      skipUpscale: body.skipUpscale === true || body.skipUpscale === "1",
      aiUpscale: body.skipUpscale === false || body.skipUpscale === "0",
      concerns: Array.isArray(body.concerns) ? body.concerns.map((line: unknown) => String(line)) : [],
      hiddenBleed: body.hiddenBleed === "ignore" ? "ignore" : undefined,
    });
    await saveQuickResult(jobId, result);
    writeJobProgress(jobId, "done");
  } catch (error) {
    const message = error instanceof Error ? error.message : "We could not build a press file from this.";
    await saveQuickResult(jobId, redFallback(message));
    writeJobProgress(jobId, "done", message);
  } finally {
    endJobRun(jobId, "quick");
  }
}

export function registerQuickPrintRoutes(app: Express) {
  startQuickPrintWatcher();

  app.get("/api/replicate/settings", (_req, res) => {
    res.json(readReplicateSettings());
  });

  app.post("/api/replicate/settings", (req, res) => {
    try {
      res.json(writeReplicateSettings(req.body || {}));
    } catch (error) {
      res.status(400).json({ message: error instanceof Error ? error.message : "Could not save the Replicate setting." });
    }
  });

  app.get("/api/quick-print/settings", (_req, res) => {
    res.json(readQuickPrintSettings());
  });

  app.post("/api/quick-print/settings", (req, res) => {
    try {
      const saved = writeQuickPrintSettings(req.body || {});
      res.json(saved);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not save the drop folder.";
      res.status(400).json({ message });
    }
  });

  app.get("/api/quick-print/recent", async (_req, res) => {
    try {
      const rows = await storage.listQuickPrint(12);
      const cards = await Promise.all(rows.map(async (row) => {
        const job = await storage.getJob(row.id);
        if (!job) return null;
        return slimQuickCard(job);
      }));
      res.json({ jobs: cards.filter(Boolean) });
    } catch (error) {
      console.error("[QUICK-PRINT] recent:", error);
      res.status(500).json({ message: "Could not load print-ready jobs." });
    }
  });

  app.get("/api/quick-print/:id", async (req, res) => {
    const job = await storage.getJob(Number(req.params.id));
    if (!job) return res.status(404).json({ message: "Job not found" });
    res.json(slimQuickCard(job));
  });

  app.post("/api/quick-print/:id/approve", async (req, res) => {
    const job = await storage.getJob(Number(req.params.id));
    if (!job) return res.status(404).json({ message: "Job not found" });
    const audit = { ...(job.auditResults || {}) } as Record<string, any>;
    const quick = { ...(audit.quickPrint || {}) };
    if (!quick.light) return res.status(400).json({ message: "This job is not a quick print." });
    quick.approved = true;
    audit.quickPrint = quick;
    if (audit.pressEngine?.passed === true && quick.pressPath) audit.overallPassed = true;
    await storage.updateJob(job.id, { auditResults: audit as AuditResults });
    const updated = await storage.getJob(job.id);
    res.json(slimQuickCard(updated || job));
  });

  app.get("/api/quick-print/:id/proof", async (req, res) => {
    const job = await storage.getJob(Number(req.params.id));
    const quick = (job?.auditResults as { quickPrint?: { proofPng?: string; proofPdf?: string } } | null)?.quickPrint;
    const kind = req.query.kind === "pdf" ? "pdf" : "png";
    const filePath = kind === "pdf" ? quick?.proofPdf : quick?.proofPng;
    if (!job || !filePath || !insideWorkspace(filePath) || !fs.existsSync(filePath)) {
      return res.status(404).json({ message: "Proof not found" });
    }
    res.setHeader("Content-Type", kind === "pdf" ? "application/pdf" : "image/png");
    res.sendFile(path.resolve(filePath));
  });

  app.post("/api/quick-print", upload.array("files", 12), async (req, res) => {
    const files = (req.files || []) as Express.Multer.File[];
    if (!files.length) return res.status(400).json({ message: "Add at least one file." });
    try {
      resolveQuickProduct(String(req.body?.productId || "a5"), Number(req.body?.customWidth), Number(req.body?.customHeight));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Choose a product size.";
      return res.status(400).json({ message });
    }
    const created = [];
    for (const file of files) {
      const filename = safeUploadName(file.originalname);
      const named = path.join(uploadDir, `quick-src-${Date.now()}-${Math.round(Math.random() * 1e6)}-${filename}`);
      fs.copyFileSync(file.path, named);
      try { fs.unlinkSync(file.path); } catch { /* multer temp already copied */ }
      const job = await storage.createJob({
        filename: file.originalname || filename,
        originalPath: named,
        fileSize: file.size,
        fileType: fileTypeForName(file.originalname || filename),
      });
      await storage.updateJob(job.id, { status: "processing" });
      const hold = beginJobRun(job.id, "quick");
      if (!hold.started) {
        created.push({ id: job.id, filename: job.filename, status: "processing" as const, inputPath: named, skip: true });
        continue;
      }
      writeJobProgress(job.id, "fitting", "Fitting the picture to the product.");
      created.push({ id: job.id, filename: job.filename, status: "processing" as const, inputPath: named, skip: false });
    }
    res.status(202).json({
      jobs: created.map(({ id, filename, status }) => ({ id, filename, status })),
    });
    for (const job of created) {
      if (job.skip) continue;
      void runJob(job.id, job.inputPath, job.filename, req.body || {});
    }
  });

  app.post("/api/print-flow/check", upload.single("file"), async (req, res) => {
    const file = req.file;
    if (!file) return res.status(400).json({ message: "Add a file to check." });
    let product;
    try {
      product = resolveQuickProduct(String(req.body?.productId || "a5"), Number(req.body?.customWidth), Number(req.body?.customHeight));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Choose a product size.";
      return res.status(400).json({ message });
    }
    const checkId = `${Date.now()}-${Math.round(Math.random() * 1e9)}`;
    const folder = path.join(uploadDir, "print-flow", checkId);
    fs.mkdirSync(folder, { recursive: true });
    const filename = safeUploadName(file.originalname);
    const stored = path.join(folder, filename);
    fs.copyFileSync(file.path, stored);
    try { fs.unlinkSync(file.path); } catch { /* copied */ }
    try {
      const report = await runIntakeCheck(stored, product.widthMm, product.heightMm);
      const manifest = {
        inputPath: stored,
        filename: file.originalname || filename,
        productId: String(req.body?.productId || product.id),
        trimW: product.widthMm,
        trimH: product.heightMm,
        items: report.items,
      };
      fs.writeFileSync(path.join(folder, "check.json"), JSON.stringify(manifest));
      res.json({ checkId, filename: manifest.filename, productLabel: product.label, ...report });
    } catch (error) {
      const message = error instanceof Error ? error.message : "The check could not finish.";
      res.status(500).json({ message });
    }
  });

  app.post("/api/print-flow/make", async (req, res) => {
    const checkId = String(req.body?.checkId || "").replace(/[^\w-]/g, "");
    const manifestPath = path.join(uploadDir, "print-flow", checkId, "check.json");
    if (!checkId || !insideWorkspace(manifestPath) || !fs.existsSync(manifestPath)) {
      return res.status(400).json({ message: "Run the quick check first." });
    }
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8")) as {
      inputPath?: string;
      filename?: string;
      productId?: string;
    };
    const inputPath = String(manifest.inputPath || "");
    if (!insideWorkspace(inputPath) || !fs.existsSync(inputPath)) {
      return res.status(400).json({ message: "The checked file is no longer available." });
    }
    const filename = String(manifest.filename || path.basename(inputPath));
    const job = await storage.createJob({
      filename,
      originalPath: inputPath,
      fileSize: fs.statSync(inputPath).size,
      fileType: fileTypeForName(filename),
    });
    await storage.updateJob(job.id, { status: "processing" });
    const hold = beginJobRun(job.id, "quick");
    if (!hold.started) {
      return res.status(202).json({ jobs: [{ id: job.id, filename, status: "processing" }] });
    }
    writeJobProgress(job.id, "fitting", "Fitting the picture to the product.");
    const body = {
      ...(req.body || {}),
      productId: String(req.body?.productId || manifest.productId || "a5"),
    };
    res.status(202).json({ jobs: [{ id: job.id, filename, status: "processing" }] });
    void runJob(job.id, inputPath, filename, body);
  });

  app.get("/api/quick-print/:id/before", async (req, res) => {
    const job = await storage.getJob(Number(req.params.id));
    const quick = (job?.auditResults as { quickPrint?: { beforePng?: string } } | null)?.quickPrint;
    const filePath = quick?.beforePng || "";
    if (!job || !filePath || !insideWorkspace(filePath) || !fs.existsSync(filePath)) {
      return res.status(404).json({ message: "Before picture not found" });
    }
    res.setHeader("Content-Type", "image/png");
    res.sendFile(path.resolve(filePath));
  });

  app.post("/api/quick-print/:id/rebleed", async (req, res) => {
    const job = await storage.getJob(Number(req.params.id));
    if (!job) return res.status(404).json({ message: "Job not found" });
    const strategy = String(req.body?.strategy || "auto");
    const allowed = new Set<string>(["auto", ...BLEED_STRATEGY_IDS]);
    if (!allowed.has(strategy)) return res.status(400).json({ message: "Choose a bleed method from the list." });
    const audit = { ...(job.auditResults || {}) } as Record<string, any>;
    const quick = { ...(audit.quickPrint || {}) };
    const pressPath = String(quick.pressPath || "");
    const trimW = Number(quick.trimWidthMm || job.productWidthMm || 0);
    const trimH = Number(quick.trimHeightMm || job.productHeightMm || 0);
    if (!pressPath || !insideWorkspace(pressPath) || !fs.existsSync(pressPath) || trimW < 20 || trimH < 20) {
      return res.status(400).json({ message: "This job does not have a press file to change." });
    }
    try {
      const changed = await runBleedReapply(pressPath, strategy, trimW, trimH, path.dirname(pressPath));
      const note = changed.bleedNote;
      quick.bleedNote = note;
      quick.bleedRevision = Number(quick.bleedRevision || 0) + 1;
      const decisions: string[] = Array.isArray(quick.decisions) ? quick.decisions.map((line: unknown) => String(line)) : [];
      const nextDecisions = decisions.filter((line) => !line.startsWith("Bleed:"));
      if (note) nextDecisions.push(note);
      quick.decisions = nextDecisions;
      let message = String(quick.customerMessage || "");
      if (note) {
        if (/Bleed:/.test(message)) message = message.replace(/Bleed:[^.]*\./, note);
        else message = `${message} ${note}`.trim();
        quick.customerMessage = message;
      }
      audit.quickPrint = quick;
      if (audit.pressEngine && typeof audit.pressEngine === "object") {
        audit.pressEngine = { ...audit.pressEngine, bleedNote: note };
      }
      await storage.updateJob(job.id, { auditResults: audit as AuditResults, correctedPath: pressPath });
      const updated = await storage.getJob(job.id);
      res.json(slimQuickCard(updated || job));
    } catch (error) {
      const message = error instanceof Error ? error.message : "The bleed could not be changed.";
      res.status(500).json({ message });
    }
  });
}
