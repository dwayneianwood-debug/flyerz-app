import type { Express } from "express";
import fs from "fs";
import path from "path";
import multer from "multer";
import { isAllowedUpload, INVALID_UPLOAD_MESSAGE } from "./illustratorIntake";
import { storage } from "./storage";
import { readQuickPrintSettings, writeQuickPrintSettings } from "./quickPrintSettings";
import {
  fileTypeForName,
  jobOutputDir,
  redFallback,
  resolveQuickProduct,
  runQuickPrintFile,
  saveQuickResult,
} from "./quickPrintRunner";
import { beginJobRun, endJobRun } from "./jobRunLock";
import { readJobProgress, writeJobProgress } from "./jobProgress";
import { startQuickPrintWatcher } from "./quickPrintWatcher";
import type { AuditResults } from "@shared/schema";
import type { QuickPrintCard } from "@shared/quickPrint";

const uploadDir = path.join(process.cwd(), "uploads");
const upload = multer({
  dest: uploadDir,
  limits: { fileSize: 50 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    if (isAllowedUpload(file.originalname, file.mimetype)) cb(null, true);
    else cb(new Error(INVALID_UPLOAD_MESSAGE));
  },
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
    };
  } | null;
}): QuickPrintCard {
  const quick = job.auditResults?.quickPrint;
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
    ...progressFields(job.id),
  };
}

function progressFields(jobId: number): { stage?: string; percent?: number; elapsedSec?: number; note?: string } {
  const progress = readJobProgress(jobId);
  if (!progress) return {};
  return {
    stage: progress.stage,
    percent: progress.percent,
    elapsedSec: progress.elapsedSec,
    note: progress.note,
  };
}

async function runJob(jobId: number, inputPath: string, filename: string, body: Record<string, unknown>) {
  const productId = String(body.productId || "a5");
  const product = resolveQuickProduct(productId, Number(body.customWidth), Number(body.customHeight));
  const quantity = Number(body.quantity);
  const notes = String(body.notes || "").slice(0, 400);
  try {
    const result = await runQuickPrintFile(inputPath, jobOutputDir(jobId), {
      productId: product.id,
      trimW: product.widthMm,
      trimH: product.heightMm,
      quantity: Number.isFinite(quantity) && quantity > 0 ? quantity : null,
      notes,
      filename,
      jobId,
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
}
