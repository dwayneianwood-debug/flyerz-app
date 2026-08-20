import type { Express } from "express";
import type { Server } from "http";
import { storage, coerceSavedBleedOptionsFromDb } from "./storage";
import { api, buildUrl } from "@shared/routes";
import type { AuditCheck, AuditResults } from "@shared/schema";
import {
  BLEED_METHOD_POST_VALUES,
  BLEED_STRATEGY_IDS,
  BLEED_STRATEGY_QUERY_VALUES,
} from "@shared/schema";
import { z } from "zod";
import multer from "multer";
import path from "path";
import fs from "fs/promises";
import {
  processFile,
  startJanitor,
  generateHealthReport,
  getQueueStatus,
  getJobQueuePosition,
  isSafeZoneLayoutRejectionMessage,
} from "./fileProcessor";
import { execSync, spawnSync } from "child_process";
import fsSync from "fs";
import os from "os";
import { getFlyerzTempRoot } from "./envPaths";
import crypto from "crypto";
import { createTask, getTask, updateTask, cleanStaleTasks } from "./taskQueue";
import { spawn } from "child_process";

const EXEC_TIMEOUT_MS = 60_000;
const COMPILE_TIMEOUT_MS = 180_000;
const COMPILE_SCRIPT = path.join(process.cwd(), "server", "compile_press_pdf.py");
const PYTHON_BIN = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");

interface PreCompileEntry {
  state: "compiling" | "ready" | "failed";
  strategy: string;
  zipPath: string;
  taskId: string;
  childPid?: number;
  error?: string;
  auditReport?: any;
  glitchyMessage?: string;
  glitchyState?: string;
  startedAt: number;
  donePromise?: Promise<void>;
  resolveDone?: () => void;
}
const preCompileCache = new Map<number, PreCompileEntry>();

function annihilateCache(jobId: number) {
  const zipPath = path.join(uploadDir, `flyerz_precompile_${jobId}.zip`);
  try { fsSync.unlinkSync(zipPath); } catch {}
  try { fsSync.unlinkSync(zipPath + ".tmp"); } catch {}
}

function cancelPreCompile(jobId: number) {
  const entry = preCompileCache.get(jobId);
  if (entry) {
    if (entry.state === "compiling" && entry.childPid) {
      try { process.kill(entry.childPid, "SIGTERM"); } catch {}
      console.log(`[FAI] Cancelled pre-compile for job ${jobId} (pid ${entry.childPid})`);
    }
    entry.state = "failed";
    entry.error = "Cancelled";
    if (entry.resolveDone) entry.resolveDone();
  }
  annihilateCache(jobId);
  preCompileCache.delete(jobId);
}

function ensureExtensionPath(originalPath: string, filename: string, jobId: number): { inputPath: string; tempSymlink: string | null } {
  const existingExt = path.extname(originalPath).toLowerCase();
  if (existingExt && [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"].includes(existingExt)) {
    return { inputPath: originalPath, tempSymlink: null };
  }
  const filenameExt = path.extname(filename).toLowerCase() || ".png";
  const symlinkPath = path.join(uploadDir, `flyerz_input_${jobId}${filenameExt}`);
  try { fsSync.unlinkSync(symlinkPath); } catch {}
  try {
    fsSync.symlinkSync(path.resolve(originalPath), symlinkPath);
    console.log(`[FAI] Created symlink: ${symlinkPath} → ${originalPath} (ext from filename: ${filenameExt})`);
  } catch (e: any) {
    fsSync.copyFileSync(originalPath, symlinkPath);
    console.log(`[FAI] Copied file: ${originalPath} → ${symlinkPath} (symlink failed: ${e.message})`);
  }
  return { inputPath: symlinkPath, tempSymlink: symlinkPath };
}

function nukeRamDisk() {
  const ramDir = getFlyerzTempRoot();
  try {
    fsSync.rmSync(ramDir, { recursive: true, force: true });
  } catch {}
  try {
    fsSync.mkdirSync(ramDir, { recursive: true });
  } catch {}
}

function spawnPreCompile(jobId: number, artworkPath: string, strategy: string, job: any) {
  cancelPreCompile(jobId);
  nukeRamDisk();

  const auditResults = job.auditResults as AuditResults | null;
  const savedOptsDebug = coerceSavedBleedOptionsFromDb((auditResults as any)?.savedBleedOptions);
  const hasCropDebug = savedOptsDebug?.cropX != null;
  console.log(`[FAI] spawnPreCompile job=${jobId} strategy=${strategy} artworkPath=${artworkPath} hasCrop=${hasCropDebug} cropCoords=${hasCropDebug ? `(${savedOptsDebug.cropX},${savedOptsDebug.cropY}) ${savedOptsDebug.cropWidth}x${savedOptsDebug.cropHeight}` : 'none'}`);
  console.log(`TRACER: [Checkpoint B2] spawnPreCompile INSIDE: job=${jobId} strategy="${strategy}" dbSelectedBleedMethod="${auditResults?.selectedBleedMethod || 'none'}" recommended="${auditResults?.recommendedBleedMethod || 'none'}"`);

  const baseName = path.parse(job.filename).name;
  const zipPath = path.join(uploadDir, `flyerz_precompile_${jobId}.zip`);
  const outputPath = path.join("uploads", `${jobId}_press_ready_precompile.pdf`);
  const statusFile = path.join(os.tmpdir(), `precompile_status_${jobId}.json`);
  const resultFile = path.join(os.tmpdir(), `precompile_result_${jobId}.json`);

  const proofPath = auditResults?.bleedProofPath || auditResults?.proofPath || "";
  const reportPath = auditResults?.healthReportPath || "";

  const { inputPath: resolvedInput, tempSymlink } = ensureExtensionPath(artworkPath, job.filename, jobId);

  const savedOpts = coerceSavedBleedOptionsFromDb(auditResults?.savedBleedOptions);
  const trimW = savedOpts.targetWidth || 148;
  const trimH = savedOpts.targetHeight || 210;

  // Booklet gutter creep shifts the raster in PDF pt space (white strip on the left). Single-page flyer compile must keep creep at 0.
  const precompileCreepMm = 0;

  const args = [
    COMPILE_SCRIPT,
    "--input", resolvedInput,
    "--output", outputPath,
    "--strategy", strategy,
    "--color-space", "cmyk",
    "--trim-w", String(trimW),
    "--trim-h", String(trimH),
    "--status-file", statusFile,
    "--result-file", resultFile,
    "--zip-output", zipPath,
    "--proof-path", proofPath,
    "--report-path", reportPath,
    "--base-name", baseName,
    "--creep-mm", String(precompileCreepMm),
  ];

  if (strategy === "colorBorder") {
    args.push("--bleed-color", sanitizeBleedBorderColor(savedOpts?.bleedBorderColor));
  }

  if (savedOpts?.cropX != null && savedOpts?.cropY != null &&
      savedOpts?.cropWidth != null && savedOpts?.cropHeight != null) {
    args.push(
      "--crop-x", String(savedOpts.cropX),
      "--crop-y", String(savedOpts.cropY),
      "--crop-w", String(savedOpts.cropWidth),
      "--crop-h", String(savedOpts.cropHeight)
    );
  }

  console.log(`TRACER: [Checkpoint C] Spawning compile subprocess for job ${jobId} — args: ${JSON.stringify(args)}`);
  if (job.originalPath && job.originalPath !== artworkPath) {
    args.push("--original-path", job.originalPath);
  }

  const task = createTask(jobId);

  let resolveDone: () => void;
  const donePromise = new Promise<void>((resolve) => { resolveDone = resolve; });

  const entry: PreCompileEntry = {
    state: "compiling",
    strategy,
    zipPath,
    taskId: task.taskId,
    startedAt: Date.now(),
    donePromise,
    resolveDone: resolveDone!,
  };
  preCompileCache.set(jobId, entry);

  const child = spawn(PYTHON_BIN, args, {
    cwd: process.cwd(),
    env: PYTHON_ENV,
    stdio: ["pipe", "pipe", "pipe"],
  });

  entry.childPid = child.pid;

  child.stdout.on("data", (data: Buffer) => {
    const lines = data.toString().trim();
    if (lines) console.log(`[COMPILE-STDOUT job=${jobId}] ${lines}`);
  });
  child.stderr.on("data", (data: Buffer) => {
    const lines = data.toString().trim();
    if (lines) console.error(`[COMPILE-STDERR job=${jobId}] ${lines}`);
  });

  const compileTimeout = setTimeout(() => {
    child.kill("SIGTERM");
    entry.state = "failed";
    entry.error = "Pre-compilation timed out";
    updateTask(task.taskId, { state: "FAILURE", message: "Pre-compilation timed out after 180 seconds" });
  }, COMPILE_TIMEOUT_MS);

  let pollInterval: ReturnType<typeof setInterval> | null = setInterval(() => {
    try {
      if (fsSync.existsSync(statusFile)) {
        const raw = fsSync.readFileSync(statusFile, "utf-8");
        const status = JSON.parse(raw);
        updateTask(task.taskId, { state: status.state, message: status.message });
      }
    } catch {}
  }, 500);

  child.on("close", async (code) => {
    clearTimeout(compileTimeout);
    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }

    try {
      if (fsSync.existsSync(resultFile)) {
        const raw = fsSync.readFileSync(resultFile, "utf-8");
        const result = JSON.parse(raw);
        if (result.success && fsSync.existsSync(zipPath)) {
          entry.state = "ready";
          entry.auditReport = result.audit_report;
          entry.glitchyMessage = result.glitchy_message;
          entry.glitchyState = result.glitchy_state;

          updateTask(task.taskId, {
            state: "COMPLETE",
            message: "Pre-compilation ready",
            outputPath: result.outputPath,
            downloadUrl: `/api/jobs/${jobId}/download-bundle?strategy=${encodeURIComponent(strategy)}`,
            auditReport: result.audit_report,
            glitchyMessage: result.glitchy_message,
            glitchyState: result.glitchy_state,
          });

          const freshJob = await storage.getJob(jobId);
          const freshAudit = (freshJob?.auditResults || auditResults || { checks: [], overallPassed: false, fixesApplied: 0, complianceReport: "" }) as AuditResults;
          const updatedResults: AuditResults = {
            ...freshAudit,
            compiledPdfPath: result.outputPath,
            compileTaskId: task.taskId,
            compiledStrategy: strategy,
            compileAuditReport: result.audit_report || undefined,
          };
          await storage.updateJob(jobId, { auditResults: updatedResults });

          console.log(`[FAI] Pre-compile READY for job ${jobId} (strategy: ${strategy} [direct], zip: ${zipPath})`);
        } else {
          entry.state = "failed";
          entry.error = result.error || "Compilation failed";
          updateTask(task.taskId, { state: "FAILURE", message: result.error || "Compilation failed" });
          console.log(`[FAI] Pre-compile FAILED for job ${jobId}: ${result.error}`);
        }
      } else {
        entry.state = "failed";
        entry.error = `Process exited with code ${code}`;
        updateTask(task.taskId, { state: "FAILURE", message: `Process exited with code ${code}` });
      }
    } catch (err: any) {
      entry.state = "failed";
      entry.error = err.message;
      updateTask(task.taskId, { state: "FAILURE", message: err.message });
    }

    try { await fs.unlink(statusFile); } catch {}
    try { await fs.unlink(resultFile); } catch {}
    const currentEntry = preCompileCache.get(jobId);
    if (tempSymlink && (!currentEntry || currentEntry.taskId === task.taskId)) {
      try { await fs.unlink(tempSymlink); } catch {}
    }

    if (entry.resolveDone) entry.resolveDone();
  });

  console.log(`[FAI] Pre-compile SPAWNED for job ${jobId} (strategy: ${strategy} [direct param], pid: ${child.pid}, taskId: ${task.taskId})`);
  return task.taskId;
}

const PYTHON_ENV: Record<string, string> = (() => {
  const e: Record<string, string> = {
    ...(process.env as Record<string, string>),
    PYTHONUNBUFFERED: "1",
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
  if (!e.FAI_TEMP_DIR?.trim()) {
    e.FAI_TEMP_DIR = getFlyerzTempRoot();
  }
  return e;
})();

/** Never send this healed-geometry artifact to the client / Glitchy */
function stripCropBoxNotInMediaBoxFromChecks(checks: unknown[] | undefined): any[] {
  const needle = /cropbox\s+not\s+in\s+mediabox/i;
  if (!checks?.length) return [];
  return checks.filter((c: any) => !needle.test(`${c?.message ?? ""} ${c?.details ?? ""} ${c?.name ?? ""}`));
}

function execPythonCapture(args: string[], label: string, timeoutMs: number = EXEC_TIMEOUT_MS): any {
  const proc = spawnSync(PYTHON_BIN, args, {
    cwd: process.cwd(),
    env: PYTHON_ENV,
    encoding: "utf8",
    timeout: timeoutMs,
    maxBuffer: 50 * 1024 * 1024,
    stdio: ["ignore", "pipe", "pipe"],
  });

  const procErr = proc.error as NodeJS.ErrnoException | undefined;
  if (procErr) {
    if (procErr.code === "ETIMEDOUT" || /TIMEOUT/i.test(String(procErr.message))) {
      throw new Error(`${label} timed out after ${timeoutMs / 1000} seconds.`);
    }
    throw new Error(`${label} failed to start Python (${PYTHON_BIN}): ${procErr.message}`);
  }

  const stdout = (proc.stdout || "").trim();
  const stderr = (proc.stderr || "").trim();
  if (proc.signal) {
    throw new Error(`${label} terminated by signal ${proc.signal}.${stderr ? ` ${stderr.slice(-500)}` : ""}`);
  }
  if (proc.status !== 0) {
    console.error(`[FAI] ${label} exit ${proc.status} stderr:\n${stderr.slice(-4000)}`);
    throw new Error(`${label} failed (exit ${proc.status}). ${stderr ? stderr.slice(-500) : stdout.slice(-300) || "See server log."}`);
  }

  try {
    const parsed = JSON.parse(stdout);
    if (parsed.success === false) throw new Error(parsed.error || `${label} failed`);
    return parsed;
  } catch (err: any) {
    if (err instanceof SyntaxError) {
      throw new Error(`${label} returned invalid JSON.`);
    }
    throw err;
  }
}

function execQuickCheck(scriptPath: string, filePath: string, fileType: string): any {
  const resultFile = path.join(os.tmpdir(), `qc_${crypto.randomBytes(8).toString("hex")}.json`);
  const args = [scriptPath, filePath, fileType, resultFile];

  const proc = spawnSync(PYTHON_BIN, args, {
    cwd: process.cwd(),
    env: PYTHON_ENV,
    encoding: "utf8",
    timeout: EXEC_TIMEOUT_MS,
    maxBuffer: 50 * 1024 * 1024,
    stdio: ["ignore", "inherit", "pipe"],
  });

  const procErr = proc.error as NodeJS.ErrnoException | undefined;
  if (procErr) {
    try { fsSync.unlinkSync(resultFile); } catch {}
    if (procErr.code === "ETIMEDOUT" || /TIMEOUT/i.test(String(procErr.message))) {
      throw new Error("Quick check timed out after 60 seconds.");
    }
    throw new Error(`Quick check failed to start Python (${PYTHON_BIN}): ${procErr.message}`);
  }

  const stderr = (proc.stderr || "").trim();
  if (proc.signal) {
    try { fsSync.unlinkSync(resultFile); } catch {}
    throw new Error(`Quick check terminated (${proc.signal}). ${stderr.slice(-400)}`);
  }
  if (proc.status !== 0) {
    try { fsSync.unlinkSync(resultFile); } catch {}
    console.error(`[FAI] Quick-check exit ${proc.status} stderr:\n${stderr.slice(-4000)}`);
    throw new Error(`Quick check failed (exit ${proc.status}). ${stderr ? stderr.slice(-500) : "See server log."}`);
  }

  try {
    const raw = fsSync.readFileSync(resultFile, "utf-8");
    return JSON.parse(raw);
  } catch {
    throw new Error("Quick check failed. No result produced. Check server console for details.");
  } finally {
    try { fsSync.unlinkSync(resultFile); } catch {}
  }
}

/** Parse JSON from multipart text fields or already-parsed JSON bodies. */
function parseJsonField(raw: unknown): any {
  if (raw == null || raw === "") return undefined;
  if (typeof raw === "object" && raw !== null && !Array.isArray(raw)) return raw;
  if (typeof raw === "string") {
    const s = raw.trim().replace(/^\uFEFF/, "");
    return JSON.parse(s);
  }
  return undefined;
}

/** Drop undefined so a partial client patch does not wipe saved DB fields. */
function omitUndefined<T extends Record<string, any>>(o: T): Partial<T> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(o)) {
    if (o[k] !== undefined) out[k] = o[k];
  }
  return out as Partial<T>;
}

/** Backup target mm from explicit FormData fields (some clients stringify bleedOptions oddly). */
function readTargetMmFromForm(body: any): { targetWidth?: number; targetHeight?: number } {
  const out: { targetWidth?: number; targetHeight?: number } = {};
  if (!body) return out;
  const twRaw = body.targetWidthMm ?? body.targetWidth_mm;
  const thRaw = body.targetHeightMm ?? body.targetHeight_mm;
  if (twRaw != null && String(twRaw).trim() !== "") {
    const tw = parseFloat(String(twRaw));
    if (!isNaN(tw) && tw > 0) out.targetWidth = Math.min(3000, tw);
  }
  if (thRaw != null && String(thRaw).trim() !== "") {
    const th = parseFloat(String(thRaw));
    if (!isNaN(th) && th > 0) out.targetHeight = Math.min(3000, th);
  }
  return out;
}

function sanitizeBleedBorderColor(raw: unknown): string {
  const s = String(raw ?? "").trim();
  const hex = s.startsWith("#") ? s.slice(1) : s;
  if (/^[0-9A-Fa-f]{6}$/.test(hex)) {
    return `#${hex.toUpperCase()}`;
  }
  return "#FFFFFF";
}

function sanitizeBleedOptions(parsed: any) {
  if (!parsed || typeof parsed !== 'object') return undefined;
  const result: any = {
    targetWidth: (parsed.targetWidth && Number(parsed.targetWidth) > 0) ? Math.min(3000, Number(parsed.targetWidth)) : null,
    targetHeight: (parsed.targetHeight && Number(parsed.targetHeight) > 0) ? Math.min(3000, Number(parsed.targetHeight)) : null,
    defaultBleedSize: Math.max(1, Math.min(15, Number(parsed.defaultBleedSize) || 5)),
    adjustableBleedSize: Math.max(1, Math.min(15, Number(parsed.adjustableBleedSize) || 5)),
    colorProfile: ['cmyk', 'rgb', 'auto'].includes(parsed.colorProfile) ? parsed.colorProfile : 'cmyk',
    outputType: ['print', 'digital'].includes(parsed.outputType) ? parsed.outputType : 'print',
    extendSolidColors: !!parsed.extendSolidColors,
    enableGradientFade: !!parsed.enableGradientFade,
    addBorder: !!parsed.addBorder,
    separateLayers: !!parsed.separateLayers,
    useClippingMasks: !!parsed.useClippingMasks,
    sampleEdgeColors: !!parsed.sampleEdgeColors,
    increaseBleedMargins: !!parsed.increaseBleedMargins,
    resizeArtwork: !!parsed.resizeArtwork,
    adjustTrimLines: !!parsed.adjustTrimLines,
    useTemplates: !!parsed.useTemplates,
    consultPrinters: !!parsed.consultPrinters,
    createMockups: !!parsed.createMockups,
    autoSafeZoneFix: parsed.autoSafeZoneFix !== false,
    enableLayoutBalancing: parsed.enableLayoutBalancing !== false,
    enableCompositionCenter: parsed.enableCompositionCenter !== false,
    enableSmartDownscale: parsed.enableSmartDownscale !== false,
    enableMarginNormalization: parsed.enableMarginNormalization !== false,
    enableToleranceSimulation: parsed.enableToleranceSimulation !== false,
    enableSpineShiftDetection: parsed.enableSpineShiftDetection !== false,
    enableCreepCompensation: parsed.enableCreepCompensation !== false,
    enableGutterCollisionCheck: parsed.enableGutterCollisionCheck !== false,
    enableWhiteEdgeRisk: parsed.enableWhiteEdgeRisk !== false,
    enablePdfxCompliance: parsed.enablePdfxCompliance !== false,
  };

  const borderHex = parsed.bleedBorderColor != null && String(parsed.bleedBorderColor).trim() !== ""
    ? sanitizeBleedBorderColor(parsed.bleedBorderColor)
    : "";
  if (borderHex) {
    result.bleedBorderColor = borderHex;
  }

  if (parsed.clientOptimized) {
    result.clientOptimized = true;
    console.log(`[FAI] Client-side print optimization active`);
  }

  if (parsed.preserveBleed) {
    result.preserveBleed = true;
    console.log(`[FAI] preserveBleed=true — scale_fill bypass requested`);
  }

  if (parsed.cropX != null && parsed.cropY != null &&
      parsed.cropWidth != null && parsed.cropHeight != null) {
    const cx = Number(parsed.cropX);
    const cy = Number(parsed.cropY);
    const cw = Number(parsed.cropWidth);
    const ch = Number(parsed.cropHeight);
    if (!isNaN(cx) && !isNaN(cy) && !isNaN(cw) && !isNaN(ch) && cw > 0 && ch > 0) {
      result.cropX = cx;
      result.cropY = cy;
      result.cropWidth = cw;
      result.cropHeight = ch;
      console.log(`[FAI] sanitizeBleedOptions: Crop coords preserved: (${cx},${cy}) ${cw}x${ch}`);
    }
  }

  return result;
}

// Configure multer for file uploads
const uploadDir = path.join(process.cwd(), "uploads");
const upload = multer({
  dest: uploadDir,
  limits: { fileSize: 50 * 1024 * 1024 }, // 50MB max
  fileFilter: (req, file, cb) => {
    const allowedTypes = ['.pdf', '.jpg', '.jpeg', '.png', '.docx', '.pptx'];
    const ext = path.extname(file.originalname).toLowerCase();
    if (allowedTypes.includes(ext)) {
      cb(null, true);
    } else {
      cb(new Error('Invalid file type. Only PDF, JPG, PNG, DOCX, and PPTX are allowed.'));
    }
  }
});

async function ensureUploadDir() {
  try {
    await fs.access(uploadDir);
  } catch {
    await fs.mkdir(uploadDir, { recursive: true });
  }
}

function isPathSafe(filePath: string): boolean {
  const resolved = path.resolve(filePath);
  const uploadsResolved = path.resolve(uploadDir);
  const cwd = path.resolve(process.cwd());
  return resolved.startsWith(uploadsResolved) || resolved.startsWith(cwd);
}

export async function registerRoutes(
  httpServer: Server,
  app: Express
): Promise<Server> {
  await ensureUploadDir();

  startJanitor(60 * 60 * 1000);

  // Get all jobs
  app.get(api.jobs.list.path, async (req, res) => {
    try {
      const jobs = await storage.getJobs();
      res.json(jobs);
    } catch (error) {
      console.error('Error fetching jobs:', error);
      res.status(500).json({ message: 'Failed to fetch jobs' });
    }
  });

  app.get('/api/queue-status', (req, res) => {
    const status = getQueueStatus();
    res.json(status);
  });

  app.get('/api/jobs/:id/queue-position', (req, res) => {
    const jobId = Number(req.params.id);
    const position = getJobQueuePosition(jobId);
    res.json({ jobId, position, queued: position !== null });
  });

  // Get single job
  app.get(api.jobs.get.path, async (req, res) => {
    try {
      const job = await storage.getJob(Number(req.params.id));
      if (!job) {
        return res.status(404).json({ message: 'Job not found' });
      }
      res.json(job);
    } catch (error) {
      console.error('Error fetching job:', error);
      res.status(500).json({ message: 'Failed to fetch job' });
    }
  });

  // Upload file
  app.post(api.jobs.upload.path, upload.single('file'), async (req, res) => {
    try {
      if (!req.file) {
        return res.status(400).json({ message: 'No file uploaded' });
      }

      const file = req.file;
      const fileType = path.extname(file.originalname).toLowerCase().replace('.', '') as any;
      const normalizedType = fileType === 'jpeg' ? 'jpg' : fileType;

      const job = await storage.createJob({
        filename: file.originalname,
        originalPath: file.path,
        fileSize: file.size,
        fileType: normalizedType,
      });

      let bleedOptions: ReturnType<typeof sanitizeBleedOptions> | undefined;
      try {
        const mm = readTargetMmFromForm(req.body);
        let merged: Record<string, any> = {};
        if (req.body.bleedOptions) {
          const parsed = parseJsonField(req.body.bleedOptions);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            merged = { ...parsed, ...mm };
          } else {
            merged = { ...mm };
          }
        } else {
          merged = { ...mm };
        }
        bleedOptions = sanitizeBleedOptions(coerceSavedBleedOptionsFromDb(merged));
      } catch (e) {
        console.warn('[FAI] Invalid bleedOptions JSON, using defaults', e);
        bleedOptions = sanitizeBleedOptions(coerceSavedBleedOptionsFromDb({}));
      }

      await storage.updateJob(job.id, { status: 'processing' });

      let quickCheckResult: any;
      try {
        // quick_check.py runs PDF geometry sanitize (CropBox/MediaBox) on disk before the 5 checks
        const QUICK_CHECK_SCRIPT = path.join(process.cwd(), 'server', 'quick_check.py');
        quickCheckResult = await execQuickCheck(QUICK_CHECK_SCRIPT, file.path, normalizedType);
      } catch (qcError: any) {
        await storage.updateJob(job.id, {
          status: 'failed',
          errorMessage: qcError.message || 'Quick check crashed',
          completedAt: new Date(),
        });
        return res.status(201).json({ jobId: job.id, filename: job.filename, status: 'failed' });
      }

      if (quickCheckResult.error) {
        await storage.updateJob(job.id, {
          status: 'failed',
          errorMessage: quickCheckResult.error,
          completedAt: new Date(),
        });
        return res.status(201).json({ jobId: job.id, filename: job.filename, status: 'failed' });
      }

      const checks: AuditCheck[] = (stripCropBoxNotInMediaBoxFromChecks(quickCheckResult.checks) || []).map((c: any) => ({
        name: c.name,
        passed: c.passed,
        message: c.message,
        autoFixed: false,
        details: c.details || '',
        severity: c.severity,
      }));

      const auditResults: AuditResults = {
        checks,
        overallPassed: checks.every((c: any) => c.passed || c.severity === "WARNING" || c.severity === "MANUAL_REVIEW"),
        fixesApplied: 0,
        complianceReport: `Quick check completed. ${checks.filter(c => c.passed).length}/${checks.length} checks passed.`,
        artworkSize: quickCheckResult.artworkSize,
        savedBleedOptions: bleedOptions,
      };

      await storage.updateJob(job.id, {
        status: 'complete',
        auditResults,
        completedAt: new Date(),
      });

      res.status(201).json({
        jobId: job.id,
        filename: job.filename,
        status: 'complete',
      });
    } catch (error) {
      console.error('Error uploading file:', error);
      if (error instanceof multer.MulterError) {
        if (error.code === 'LIMIT_FILE_SIZE') {
          return res.status(413).json({
            message: 'File too large. Maximum size is 50MB.',
            code: 'FILE_TOO_LARGE'
          });
        }
      }
      res.status(500).json({ message: 'Failed to upload file' });
    }
  });

  app.post('/api/remove-background', upload.single('file'), async (req, res) => {
    try {
      if (!req.file) {
        return res.status(400).json({ message: 'No file uploaded' });
      }

      const file = req.file;
      const ext = path.extname(file.originalname).toLowerCase();
      if (!['.png', '.jpg', '.jpeg'].includes(ext)) {
        return res.status(400).json({ message: 'Background removal only supports PNG and JPG images' });
      }

      const outputFilename = `${path.basename(file.path)}_nobg.png`;
      const outputPath = path.join(uploadDir, outputFilename);
      const REMOVE_BG_SCRIPT = path.join(process.cwd(), 'server', 'remove_bg.py');

      const result: any = await new Promise((resolve, reject) => {
        const child = spawn(PYTHON_BIN, [REMOVE_BG_SCRIPT, file.path, outputPath], {
          timeout: 60000,
          stdio: ['pipe', 'pipe', 'pipe'],
          env: PYTHON_ENV,
        });
        let stdout = '';
        let stderr = '';
        child.stdout.on('data', (d: Buffer) => { stdout += d.toString(); });
        child.stderr.on('data', (d: Buffer) => { stderr += d.toString(); });
        child.on('close', (code: number) => {
          if (stderr) console.error('[RemoveBG] stderr:', stderr);
          try {
            const parsed = JSON.parse(stdout);
            resolve(parsed);
          } catch {
            reject(new Error(`Remove BG script failed (code ${code}): ${stderr || stdout}`));
          }
        });
        child.on('error', (err: Error) => reject(err));
      });

      if (!result.success) {
        return res.status(500).json({ message: result.error || 'Background removal failed' });
      }

      const baseName = path.basename(file.originalname, ext);
      res.json({
        success: true,
        downloadUrl: `/api/remove-background/download/${outputFilename}`,
        filename: `${baseName}_nobg.png`,
        width: result.width,
        height: result.height,
        fileSize: result.fileSize,
      });
    } catch (error: any) {
      console.error('[RemoveBG] Error:', error);
      res.status(500).json({ message: error.message || 'Background removal failed' });
    }
  });

  app.get('/api/remove-background/download/:filename', async (req, res) => {
    try {
      const filename = req.params.filename;
      if (/[\/\\]/.test(filename) || filename.includes('..')) {
        return res.status(403).json({ message: 'Forbidden' });
      }
      if (!filename.endsWith('_nobg.png')) {
        return res.status(400).json({ message: 'Invalid file' });
      }
      const filePath = path.join(uploadDir, filename);
      const resolved = path.resolve(filePath);
      if (!resolved.startsWith(path.resolve(uploadDir) + path.sep)) {
        return res.status(403).json({ message: 'Forbidden' });
      }
      await fs.access(filePath);
      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'no-cache');
      const stream = fsSync.createReadStream(filePath);
      stream.pipe(res);
    } catch {
      res.status(404).json({ message: 'File not found' });
    }
  });

  // Quick pre-flight check (Step 1 — read-only, no corrections)
  app.post('/api/quick-check', upload.single('file'), async (req, res) => {
    try {
      if (!req.file) {
        return res.status(400).json({ message: 'No file uploaded' });
      }

      const file = req.file;
      const fileType = path.extname(file.originalname).toLowerCase().replace('.', '');
      const normalizedType = fileType === 'jpeg' ? 'jpg' : fileType;

      // quick_check.py sanitizes PDF page boxes in-place before pre-flight telemetry
      const QUICK_CHECK_SCRIPT = path.join(process.cwd(), 'server', 'quick_check.py');
      const result = await execQuickCheck(QUICK_CHECK_SCRIPT, file.path, normalizedType);

      if (result.error) {
        return res.status(500).json({ message: result.error });
      }

      if (result.checks) {
        result.checks = stripCropBoxNotInMediaBoxFromChecks(result.checks);
        result.allPassed = result.checks.every((c: any) => c.passed);
        result.passCount = result.checks.filter((c: any) => c.passed).length;
        result.failCount = result.checks.filter((c: any) => !c.passed).length;
      }

      // Return results along with the stored file path so the user can
      // later submit it to the full pipeline (Final Check)
      res.json({
        ...result,
        storedFilename: path.basename(file.path),
        originalFilename: file.originalname,
        fileType: normalizedType,
      });
    } catch (error: any) {
      console.error('[FAI] Quick check error:', error);
      res.status(500).json({ message: error.message || 'Quick check failed' });
    }
  });

  // Submit a file from quick-check to the full pipeline (auto-fix)
  app.post('/api/quick-check/fix', async (req, res) => {
    try {
      const { storedFilename, originalFilename, fileType, bleedOptions: rawBleedOptions } = req.body;
      if (!storedFilename || !originalFilename || !fileType) {
        return res.status(400).json({ message: 'Missing storedFilename, originalFilename, or fileType' });
      }

      const filePath = path.join(uploadDir, storedFilename);
      try {
        await fs.access(filePath);
      } catch {
        return res.status(404).json({ message: 'Uploaded file not found. Please re-upload.' });
      }

      const stat = await fs.stat(filePath);

      const job = await storage.createJob({
        filename: originalFilename,
        originalPath: filePath,
        fileSize: stat.size,
        fileType: fileType,
      });

      let bleedOptions = undefined;
      try {
        if (rawBleedOptions) {
          const parsed = parseJsonField(rawBleedOptions);
          if (parsed && typeof parsed === "object") {
            bleedOptions = sanitizeBleedOptions(parsed);
          }
        }
      } catch (e) {
        console.warn('[FAI] Invalid bleedOptions in quick-check fix, using defaults', e);
      }

      processFile(job.id, true, bleedOptions).catch((error: Error) => {
        console.error(`Error processing job ${job.id}:`, error);
        storage.updateJob(job.id, {
          status: 'failed',
          errorMessage: error.message,
          completedAt: new Date(),
        });
      });

      res.status(201).json({
        jobId: job.id,
        filename: originalFilename,
        status: 'pending',
      });
    } catch (error: any) {
      console.error('[FAI] Quick-check fix error:', error);
      res.status(500).json({ message: error.message || 'Failed to start fix' });
    }
  });

  // Process/fix file (triggered by user clicking Download/Process)
  app.post(api.jobs.process.path, async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      
      if (!job) {
        return res.status(404).json({ message: 'Job not found' });
      }

      if (job.status === 'processing') {
        return res.status(400).json({ message: 'Job is already being processed' });
      }

      let bleedOptions = sanitizeBleedOptions(
        coerceSavedBleedOptionsFromDb((job.auditResults as any)?.savedBleedOptions),
      );
      try {
        if (req.body.bleedOptions) {
          const parsed = parseJsonField(req.body.bleedOptions);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            bleedOptions = sanitizeBleedOptions({
              ...(bleedOptions || {}),
              ...omitUndefined(parsed),
            });
          }
        }
        const mm = readTargetMmFromForm(req.body);
        if (Object.keys(mm).length > 0) {
          bleedOptions = sanitizeBleedOptions({
            ...(bleedOptions || {}),
            ...mm,
          });
        }
      } catch (e) {
        console.warn('[FAI] Invalid bleedOptions in process request', e);
      }

      if (req.body.cropX !== undefined && req.body.cropY !== undefined &&
          req.body.cropWidth !== undefined && req.body.cropHeight !== undefined) {
        if (!bleedOptions) bleedOptions = {} as any;
        (bleedOptions as any).cropX = Number(req.body.cropX);
        (bleedOptions as any).cropY = Number(req.body.cropY);
        (bleedOptions as any).cropWidth = Number(req.body.cropWidth);
        (bleedOptions as any).cropHeight = Number(req.body.cropHeight);
        console.log(`[FAI] Mockup Killer crop: (${req.body.cropX},${req.body.cropY}) ${req.body.cropWidth}x${req.body.cropHeight}`);
      }

      const finalCropActive = (bleedOptions as any)?.cropX != null && (bleedOptions as any)?.cropWidth > 0;
      bleedOptions = sanitizeBleedOptions(coerceSavedBleedOptionsFromDb(bleedOptions ?? {}));
      console.log(`[FAI] processFile handoff job=${jobId}: hasCrop=${finalCropActive}${finalCropActive ? `, crop=(${(bleedOptions as any)?.cropX},${(bleedOptions as any)?.cropY}) ${(bleedOptions as any)?.cropWidth}x${(bleedOptions as any)?.cropHeight}` : ''}, targetSize=${(bleedOptions as any)?.targetWidth || '?'}x${(bleedOptions as any)?.targetHeight || '?'}mm`);

      try {
        await processFile(jobId, true, bleedOptions);
      } catch (error: any) {
        console.error(`Error processing job ${jobId}:`, error);
        const msg = error instanceof Error ? error.message : String(error);
        const layoutRejection = isSafeZoneLayoutRejectionMessage(msg);
        await storage.updateJob(jobId, {
          status: 'failed',
          errorMessage: msg,
          completedAt: new Date(),
        });
        return res.status(layoutRejection ? 422 : 500).json({
          success: false,
          message: msg || 'Processing failed',
          jobId: jobId,
        });
      }

      res.json({
        message: 'Processing complete',
        jobId: jobId,
      });
    } catch (error) {
      console.error('Error starting processing:', error);
      res.status(500).json({ message: 'Failed to start processing' });
    }
  });

  // Download file
  app.get(api.jobs.download.path, async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const type = req.params.type as 'original' | 'corrected' | 'report';
      
      const job = await storage.getJob(jobId);
      if (!job) {
        return res.status(404).json({ message: 'Job not found' });
      }

      let filePath: string;
      let filename: string;

      if (type === 'original') {
        filePath = job.originalPath;
        filename = job.filename;
      } else if (type === 'corrected') {
        if (!job.correctedPath) {
          return res.status(404).json({ message: 'Corrected file not available' });
        }
        filePath = job.correctedPath;
        filename = `corrected_${job.filename}`;
      } else if (type === 'report') {
        if (!job.auditResults) {
          return res.status(404).json({ message: 'Report not available' });
        }
        const report = job.auditResults.complianceReport;
        res.setHeader('Content-Type', 'text/plain');
        res.setHeader('Content-Disposition', `attachment; filename="compliance_report_${job.id}.txt"`);
        return res.send(report);
      } else if (type === 'press-ready') {
        const auditResults = job.auditResults as AuditResults | null;
        const compiledPath = auditResults?.compiledPdfPath;
        if (!compiledPath || !isPathSafe(compiledPath)) {
          return res.status(404).json({ message: 'No compiled PDF available' });
        }
        try { await fs.access(compiledPath); } catch {
          return res.status(404).json({ message: 'Compiled PDF file not found on disk' });
        }
        const downloadName = `Print Ready Artwork.pdf`;
        return res.download(compiledPath, downloadName);
      } else {
        return res.status(400).json({ message: 'Invalid download type' });
      }

      if (!isPathSafe(filePath)) {
        return res.status(403).json({ message: 'Access denied' });
      }

      try {
        await fs.access(filePath);
      } catch {
        return res.status(404).json({ message: 'File not found on server' });
      }

      res.download(filePath, filename);
    } catch (error) {
      console.error('Error downloading file:', error);
      res.status(500).json({ message: 'Failed to download file' });
    }
  });

  app.get('/api/jobs/:id/health-report', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      if (!job) {
        return res.status(404).json({ message: 'Job not found' });
      }

      const audit = job.auditResults as AuditResults | null;
      if (!audit || !audit.checks || audit.checks.length === 0) {
        return res.status(404).json({ message: 'No audit results available for report generation' });
      }

      let reportPath = audit.healthReportPath;
      let fileExists = false;

      if (reportPath && isPathSafe(reportPath)) {
        try {
          await fs.access(reportPath);
          fileExists = true;
        } catch {}
      }

      if (!fileExists) {
        const uploadsDir = path.resolve('uploads');
        const basename = path.basename(job.filename, path.extname(job.filename));
        reportPath = path.join(uploadsDir, `Flyerz_Health_Report_${basename}.pdf`);

        try {
          const proofPaths = audit.proofPaths;
          const proofPath = audit.proofPath;
          const artworkForReport = audit.bleedProofPath || proofPath;
          generateHealthReport(audit.checks, job.filename, reportPath, proofPath, proofPaths, artworkForReport || undefined);
          console.log(`[FAI] Health report regenerated on-the-fly: ${reportPath}`);

          const updatedAudit = { ...audit, healthReportPath: reportPath };
          await storage.updateJob(jobId, { auditResults: updatedAudit });
        } catch (genErr) {
          console.error('[FAI] Health report generation failed:', genErr);
          return res.status(500).json({ message: 'Failed to generate health report' });
        }
      }

      res.download(reportPath!, `Flyerz.co.za Artwork Intellegence Proof and Report.pdf`);
    } catch (error) {
      console.error('Error downloading health report:', error);
      res.status(500).json({ message: 'Failed to download health report' });
    }
  });

  const JOB_REPORT_SCRIPT = path.join(process.cwd(), "server", "job_report_pdf.py");

  /** Per-job PDF: live audit telemetry (distinct from static GET /api/checks-guide). */
  app.get("/api/jobs/:id/intelligence-report", async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      if (!Number.isFinite(jobId) || jobId <= 0) {
        return res.status(400).json({ message: "Invalid job id" });
      }
      const job = await storage.getJob(jobId);
      if (!job) {
        return res.status(404).json({ message: "Job not found" });
      }

      const audit = job.auditResults as AuditResults | null | undefined;
      const ctx = {
        jobId,
        filename: job.filename,
        generatedAt: new Date().toISOString(),
        jobStatus: job.status,
        overallPassed: audit?.overallPassed ?? false,
        fixesApplied: audit?.fixesApplied ?? 0,
        checks: audit?.checks ?? [],
        autoHealEvent: audit?.autoHealEvent ?? null,
        artworkSize: audit?.artworkSize ?? null,
        originalDpi: audit?.originalDpi ?? null,
        aiEnhanced: audit?.aiEnhanced ?? null,
        rightSafety: audit?.rightSafety ?? null,
        criticalSafeZone: audit?.criticalSafeZone ?? null,
      };

      const outPdf = path.join(os.tmpdir(), `flyerz_job_report_${jobId}_${Date.now()}.pdf`);
      const ctxPath = path.join(os.tmpdir(), `flyerz_job_report_ctx_${jobId}_${Date.now()}.json`);
      await fs.writeFile(ctxPath, JSON.stringify(ctx), "utf8");
      try {
        execPythonCapture([JOB_REPORT_SCRIPT, outPdf, ctxPath], "JobReportPdf");
      } finally {
        try {
          await fs.unlink(ctxPath);
        } catch {
          /* ignore */
        }
      }

      const safeStub = path.basename(job.filename, path.extname(job.filename)).replace(/[^\w.\-]+/g, "_") || "artwork";
      res.setHeader("Content-Type", "application/pdf");
      res.setHeader(
        "Content-Disposition",
        `attachment; filename="Flyerz_Intelligence_Job_${jobId}_${safeStub}.pdf"`,
      );
      res.setHeader("Cache-Control", "private, no-store, must-revalidate");
      res.setHeader("Pragma", "no-cache");
      res.setHeader("Expires", "0");

      const { createReadStream } = await import("fs");
      const stream = createReadStream(outPdf);
      stream.on("error", (err) => {
        console.error("[FAI] Job intelligence report stream error:", err);
        if (!res.headersSent) res.status(500).json({ message: "Failed to read generated PDF" });
      });
      res.on("close", () => {
        fs.unlink(outPdf).catch(() => {});
      });
      stream.pipe(res);
    } catch (error) {
      console.error("[FAI] Job intelligence report failed:", error);
      if (!res.headersSent) {
        res.status(500).json({ message: "Failed to generate job intelligence report" });
      }
    }
  });

  // Get visual proof image
  app.get('/api/jobs/:id/proof', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      if (!job) {
        return res.status(404).json({ message: 'Job not found' });
      }

      const pageIndex = parseInt(req.query.page as string) || 0;
      const auditResults = job.auditResults as any;

      const selectedMethod = auditResults?.selectedBleedMethod;
      if (selectedMethod && selectedMethod !== "auto" && pageIndex === 0) {
        const variantPath = auditResults?.bleedVariants?.[selectedMethod];
        if (variantPath && isPathSafe(variantPath)) {
          try {
            const stat = fsSync.statSync(variantPath);
            if (stat.size > 0) {
              console.log(`[FAI] Serving proof from selected strategy variant: ${selectedMethod} → ${variantPath}`);
              res.setHeader('Content-Type', 'image/png');
              res.setHeader('Cache-Control', 'no-cache');
              res.setHeader('X-Proof-Page-Count', '1');
              res.setHeader('X-Proof-Strategy', selectedMethod);
              const { createReadStream } = await import('fs');
              createReadStream(variantPath).pipe(res);
              return;
            }
          } catch {}
        }
      }

      let proofPaths: string[] = auditResults?.proofPaths || (auditResults?.proofPath ? [auditResults.proofPath] : []);

      const verifiedPaths: string[] = [];
      for (const p of proofPaths) {
        try {
          const stat = fsSync.statSync(p);
          if (stat.size > 0) verifiedPaths.push(p);
        } catch {}
      }
      proofPaths = verifiedPaths;

      if (proofPaths.length === 0) {
        const artworkPath = job.correctedPath || job.originalPath;
        if (artworkPath) {
          const dir = path.dirname(artworkPath);
          const base = path.basename(artworkPath, path.extname(artworkPath));
          const singleCandidates = [
            path.join(dir, `${base}_proof.png`),
            path.join(dir, `${base}.png`),
          ];
          for (const candidate of singleCandidates) {
            try {
              const stat = fsSync.statSync(candidate);
              if (stat.size > 0) {
                proofPaths = [candidate];
                break;
              }
            } catch {}
          }

          if (proofPaths.length === 0) {
            const multiPages: string[] = [];
            for (let pg = 1; pg <= 20; pg++) {
              const pgPath = path.join(dir, `${base}_proof${pg}.png`);
              try {
                const stat = fsSync.statSync(pgPath);
                if (stat.size > 0) multiPages.push(pgPath);
                else break;
              } catch { break; }
            }
            if (multiPages.length > 0) proofPaths = multiPages;
          }
        }
      }

      if (proofPaths.length === 0) {
        const artworkFile = job.correctedPath || job.originalPath;
        if (artworkFile) {
          try {
            await fs.access(artworkFile);
            const ext = path.extname(artworkFile).toLowerCase();
            if (['.png', '.jpg', '.jpeg'].includes(ext)) {
              proofPaths = [artworkFile];
            } else if (ext === '.pdf') {
              const proofBase = path.join(path.dirname(artworkFile), path.basename(artworkFile, path.extname(artworkFile)) + '_proof.png');
              try {
                const escapedInput = artworkFile.replace(/'/g, "'\\''");
                const escapedOutput = proofBase.replace(/'/g, "'\\''");
                execSync(
                  `${PYTHON_BIN} -c "import sys; sys.path.insert(0, 'server'); from smart_bleed import generate_visual_proof; generate_visual_proof('${escapedInput}', '${escapedOutput}')"`,
                  { timeout: 30000, cwd: process.cwd(), env: PYTHON_ENV, stdio: ['pipe', 'pipe', 'inherit'] }
                );

                try {
                  const stat = fsSync.statSync(proofBase);
                  if (stat.size > 0) {
                    proofPaths = [proofBase];
                    console.log(`[FAI] Visual proof regenerated on-the-fly: ${proofBase}`);
                  }
                } catch {
                  const multiPages: string[] = [];
                  const proofStem = proofBase.replace(/\.png$/, '');
                  for (let pg = 1; pg <= 20; pg++) {
                    const pgPath = `${proofStem}${pg}.png`;
                    try {
                      const stat = fsSync.statSync(pgPath);
                      if (stat.size > 0) multiPages.push(pgPath);
                      else break;
                    } catch { break; }
                  }
                  if (multiPages.length > 0) {
                    proofPaths = multiPages;
                    console.log(`[FAI] Visual proof regenerated on-the-fly: ${multiPages.length} page(s)`);
                  }
                }
              } catch (genErr: any) {
                console.warn('[FAI] Proof regeneration failed:', genErr.message || genErr);
              }
            }
          } catch {}
        }
      }

      if (proofPaths.length === 0) {
        return res.status(404).json({ message: 'Visual proof not available' });
      }

      if (pageIndex < 0 || pageIndex >= proofPaths.length) {
        return res.status(404).json({ message: `Page ${pageIndex} not found. Available pages: 0-${proofPaths.length - 1}` });
      }

      const targetPath = proofPaths[pageIndex];
      if (!isPathSafe(targetPath)) {
        return res.status(403).json({ message: 'Access denied' });
      }
      try {
        await fs.access(targetPath);
      } catch {
        return res.status(404).json({ message: 'Visual proof file not found on server' });
      }

      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'public, max-age=3600');
      res.setHeader('X-Proof-Page-Count', String(proofPaths.length));
      const { createReadStream } = await import('fs');
      createReadStream(targetPath).pipe(res);
    } catch (error) {
      console.error('Error serving proof image:', error);
      res.status(500).json({ message: 'Failed to serve proof image' });
    }
  });

  app.get('/api/jobs/:id/comparison', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      if (!job) {
        return res.status(404).json({ message: 'Job not found' });
      }

      const auditResults = job.auditResults as any;
      const comparisonPath = auditResults?.comparisonPath;
      if (!comparisonPath) {
        return res.status(404).json({ message: 'Sign-off comparison not available' });
      }

      if (!isPathSafe(comparisonPath)) {
        return res.status(403).json({ message: 'Access denied' });
      }

      const selectedMethod = auditResults?.selectedBleedMethod;
      if (selectedMethod && selectedMethod !== "auto") {
        const variantPath = auditResults?.bleedVariants?.[selectedMethod];
        if (variantPath && isPathSafe(variantPath)) {
          try {
            const stat = fsSync.statSync(variantPath);
            if (stat.size > 0) {
              const dynamicCompPath = path.join(
                path.dirname(comparisonPath),
                `comparison_dynamic_${jobId}_${selectedMethod}.png`
              );

              const compArgs = [
                path.join(process.cwd(), "server", "generate_comparison.py"),
                "--original", job.originalPath || "",
                "--variant", variantPath,
                "--output", dynamicCompPath,
                "--method", selectedMethod,
              ];

              try {
                await fs.access(dynamicCompPath);
                const dynStat = fsSync.statSync(dynamicCompPath);
                const varStat = fsSync.statSync(variantPath);
                if (dynStat.size > 0 && dynStat.mtimeMs >= varStat.mtimeMs) {
                  console.log(`[FAI] Serving cached dynamic comparison for strategy '${selectedMethod}'`);
                  res.setHeader('Content-Type', 'image/png');
                  res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
                  const { createReadStream } = await import('fs');
                  createReadStream(dynamicCompPath).pipe(res);
                  return;
                }
              } catch {}

              try {
                const { execFile } = await import('child_process');
                const { promisify } = await import('util');
                const execFileAsync = promisify(execFile);
                await execFileAsync(PYTHON_BIN, compArgs, { timeout: 30000 });
                try {
                  const dynStat = fsSync.statSync(dynamicCompPath);
                  if (dynStat.size > 0) {
                    console.log(`[FAI] Generated dynamic comparison for strategy '${selectedMethod}'`);
                    res.setHeader('Content-Type', 'image/png');
                    res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
                    const { createReadStream } = await import('fs');
                    createReadStream(dynamicCompPath).pipe(res);
                    return;
                  }
                } catch {}
              } catch (genErr: any) {
                console.error(`[FAI] Dynamic comparison generation failed: ${genErr.message}`);
              }
            }
          } catch {}
        }
      }

      try {
        await fs.access(comparisonPath);
      } catch {
        return res.status(404).json({ message: 'Sign-off comparison file not found on server' });
      }

      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
      const { createReadStream } = await import('fs');
      createReadStream(comparisonPath).pipe(res);
    } catch (error) {
      console.error('Error serving comparison image:', error);
      res.status(500).json({ message: 'Failed to serve comparison image' });
    }
  });

  // Generate bleed preview with trim/cut lines
  const BLEED_PREVIEW_SCRIPT = path.join(process.cwd(), "server", "bleed_preview.py");

  app.get('/api/jobs/:id/bleed-preview', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      if (!job) {
        return res.status(404).json({ message: 'Job not found' });
      }

      if (!job.correctedPath) {
        return res.status(404).json({ message: 'Corrected file not available. Process the file first.' });
      }

      try {
        await fs.access(job.correctedPath);
      } catch {
        return res.status(404).json({ message: 'Corrected file not found on server' });
      }

      const strategy = (req.query.strategy as string) || "auto";
      const auditResults = job.auditResults as AuditResults | null;
      const validStrategies = [...BLEED_STRATEGY_QUERY_VALUES];

      let previewSourcePath = job.correctedPath!;
      let previewFileType = job.fileType || 'pdf';

      if (strategy !== "auto" && (validStrategies as readonly string[]).includes(strategy)) {
        const variantPath = auditResults?.bleedVariants?.[strategy as keyof NonNullable<AuditResults["bleedVariants"]>];
        if (variantPath && isPathSafe(variantPath)) {
          try {
            await fs.access(variantPath);
            previewSourcePath = variantPath;
            previewFileType = path.extname(variantPath).replace('.', '') || 'png';
            console.log(`[FAI] Bleed preview using variant for strategy '${strategy}': ${variantPath}`);
          } catch {
            console.warn(`[FAI] Variant file for '${strategy}' not found, falling back to corrected`);
          }
        }
      }

      const bleedMm = parseFloat(req.query.bleed as string) || 5;
      const basename = path.basename(job.filename, path.extname(job.filename));
      const previewFilename = `bleed_preview_${jobId}_${Date.now()}.png`;
      const previewPath = path.join(uploadDir, previewFilename);

      const savedOpts = coerceSavedBleedOptionsFromDb((job.auditResults as any)?.savedBleedOptions);
      const targetWidth = String(savedOpts.targetWidth ?? 148);
      const targetHeight = String(savedOpts.targetHeight ?? 210);

      const result = execPythonCapture([
        BLEED_PREVIEW_SCRIPT, previewSourcePath, previewPath,
        previewFileType, String(bleedMm), targetWidth, targetHeight
      ], "BleedPreview");

      res.json({
        ...result,
        previewUrls: result.pages.map((p: any) => ({
          page: p.page,
          url: `/api/jobs/${jobId}/bleed-preview-image/${path.basename(p.previewPath)}`,
          downloadUrl: `/api/jobs/${jobId}/bleed-preview-download/${path.basename(p.previewPath)}?name=${encodeURIComponent(`${basename}_bleed_preview_page${p.page}.png`)}`,
          totalSize_mm: p.totalSize_mm,
          trimSize_mm: p.trimSize_mm,
          bleed_mm: p.bleed_mm,
        })),
      });
    } catch (error) {
      console.error('Error generating bleed preview:', error);
      res.status(500).json({ message: error instanceof Error ? error.message : 'Failed to generate bleed preview' });
    }
  });

  // Serve bleed preview image
  app.get('/api/jobs/:id/bleed-preview-image/:filename', async (req, res) => {
    try {
      const filename = req.params.filename;
      if (filename.includes('..') || filename.includes('/')) {
        return res.status(400).json({ message: 'Invalid filename' });
      }
      const filePath = path.join(uploadDir, filename);
      try {
        await fs.access(filePath);
      } catch {
        return res.status(404).json({ message: 'Bleed preview image not found' });
      }
      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'no-cache');
      const { createReadStream } = await import('fs');
      createReadStream(filePath).pipe(res);
    } catch (error) {
      res.status(500).json({ message: 'Failed to serve bleed preview' });
    }
  });

  // Download bleed preview image
  app.get('/api/jobs/:id/bleed-preview-download/:filename', async (req, res) => {
    try {
      const filename = req.params.filename;
      if (filename.includes('..') || filename.includes('/')) {
        return res.status(400).json({ message: 'Invalid filename' });
      }
      const filePath = path.join(uploadDir, filename);
      try {
        await fs.access(filePath);
      } catch {
        return res.status(404).json({ message: 'Bleed preview file not found' });
      }
      const downloadName = (req.query.name as string) || filename;
      res.download(filePath, downloadName);
    } catch (error) {
      res.status(500).json({ message: 'Failed to download bleed preview' });
    }
  });

  app.get('/api/jobs/:id/bleed-variant/:method', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const method = req.params.method;
      const validMethods = [...BLEED_STRATEGY_IDS];
      if (!(validMethods as readonly string[]).includes(method)) {
        return res.status(400).json({ message: `Invalid bleed method: ${method}. Valid: ${validMethods.join(", ")}` });
      }
      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });

      const auditResults = job.auditResults as AuditResults | null;
      const variantPath = auditResults?.bleedVariants?.[method as keyof NonNullable<AuditResults["bleedVariants"]>];
      if (!variantPath) {
        return res.status(404).json({ message: `No variant found for method: ${method}` });
      }

      if (!isPathSafe(variantPath)) {
        return res.status(403).json({ message: "Invalid variant file path" });
      }

      try {
        await fs.access(variantPath);
      } catch {
        return res.status(404).json({ message: `Variant file not found on disk` });
      }

      const ext = path.extname(variantPath).toLowerCase();
      const mimeMap: Record<string, string> = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf" };
      res.setHeader("Content-Type", mimeMap[ext] || "application/octet-stream");
      res.setHeader("Cache-Control", method === "colorBorder" ? "no-store" : "public, max-age=3600");
      const { createReadStream } = await import("fs");
      createReadStream(variantPath).pipe(res);
    } catch (error) {
      console.error("[FAI] Bleed variant fetch failed:", error);
      res.status(500).json({ message: "Failed to fetch bleed variant" });
    }
  });

  app.post('/api/jobs/:id/select-bleed-method', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const { method } = req.body;
      const validMethods = [...BLEED_METHOD_POST_VALUES];
      if (!method || !(validMethods as readonly string[]).includes(method)) {
        return res.status(400).json({ message: `Invalid method: ${method}` });
      }
      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });

      const auditResults = job.auditResults as AuditResults | null;
      if (!auditResults) return res.status(400).json({ message: "Job has no audit results" });

      if (method !== "auto") {
        const variantPath = auditResults.bleedVariants?.[method as keyof NonNullable<AuditResults["bleedVariants"]>];
        if (!variantPath) {
          console.log(`[FAI] No variant preview for method '${method}' — proceeding with compile from original`);
        } else {
          console.log(`[FAI] Selected bleed method '${method}' (variant preview: ${variantPath})`);
        }
      }

      const previousStrategy = auditResults.selectedBleedMethod || "auto";
      console.log(`TRACER: [Checkpoint A] select-bleed-method: job=${jobId} previousStrategy="${previousStrategy}" → newStrategy="${method}"`);

      const selectSavedOpts = coerceSavedBleedOptionsFromDb((auditResults as any)?.savedBleedOptions);
      const incomingColor = req.body?.bleedBorderColor;
      if (method === "colorBorder") {
        selectSavedOpts.bleedBorderColor = sanitizeBleedBorderColor(incomingColor);
      }

      const updatedResults: AuditResults = {
        ...auditResults,
        selectedBleedMethod: method as AuditResults["selectedBleedMethod"],
        savedBleedOptions: selectSavedOpts,
      };

      if (method === "colorBorder") {
        const existingVariant = (updatedResults.bleedVariants as any)?.colorBorder;
        const preBleedForVariant = (updatedResults as any).preBleedPath;
        const SMART_BLEED_SCRIPT = path.join(process.cwd(), "server", "smart_bleed.py");
        const rewriteSrc = (preBleedForVariant && isPathSafe(preBleedForVariant) && fsSync.existsSync(preBleedForVariant))
          ? preBleedForVariant
          : "";
        const rewriteDest = (existingVariant && isPathSafe(existingVariant))
          ? existingVariant
          : (preBleedForVariant ? `${String(preBleedForVariant).replace(/\.[^.]+$/, "")}_variant_colorborder.png` : "");
        if (rewriteSrc && rewriteDest) {
          try {
            const rewrite = spawnSync(PYTHON_BIN, [
              SMART_BLEED_SCRIPT,
              "--rewrite-color-border",
              rewriteSrc,
              rewriteDest,
              selectSavedOpts.bleedBorderColor,
              "300",
            ], { cwd: process.cwd(), env: PYTHON_ENV, timeout: 20000, encoding: "utf8" });
            if (rewrite.status === 0) {
              updatedResults.bleedVariants = {
                ...(updatedResults.bleedVariants || {}),
                colorBorder: rewriteDest,
              };
              console.log(`[FAI] colorBorder variant rewritten: ${rewriteDest} colour=${selectSavedOpts.bleedBorderColor}`);
            } else {
              console.warn(`[FAI] colorBorder variant rewrite failed: ${rewrite.stderr || rewrite.stdout}`);
            }
          } catch (rewriteErr) {
            console.warn("[FAI] colorBorder variant rewrite error:", rewriteErr);
          }
        }
      }

      await storage.updateJob(jobId, { auditResults: updatedResults });

      const selectHasCrop = !!(selectSavedOpts?.cropWidth && selectSavedOpts?.cropHeight);
      const preBleedPath = (updatedResults as any).preBleedPath;
      let artworkPath = selectHasCrop ? job.originalPath : (preBleedPath || job.originalPath);
      if (preBleedPath && !selectHasCrop) {
        try { await fs.access(preBleedPath); } catch {
          console.log(`[FAI] preBleedPath missing on disk (${preBleedPath}), falling back to originalPath`);
          artworkPath = job.originalPath;
        }
      }
      console.log(`DEBUG: select-bleed-method Job #${jobId}. Source: ${artworkPath}, Manual Crop Active: ${selectHasCrop}, preBleedPath: ${preBleedPath || 'NONE'}`);

      const jobForCompile = { ...job, auditResults: updatedResults };
      if (artworkPath && job.status === "complete") {
        try {
          await fs.access(artworkPath);
          console.log(`TRACER: [Checkpoint B] spawnPreCompile: job=${jobId} strategy="${method}" artworkPath="${artworkPath}"`);
          spawnPreCompile(jobId, artworkPath, method, jobForCompile);
        } catch (e) {
          console.log(`[FAI] Pre-compile skipped for job ${jobId}: artwork not accessible`);
        }
      }

      const timestamp = Date.now();
      const cached = preCompileCache.get(jobId);
      res.json({ success: true, strategy: method, selectedMethod: method, timestamp, preCompileTaskId: cached?.taskId });
    } catch (error) {
      console.error("[FAI] Select bleed method failed:", error);
      res.status(500).json({ message: "Failed to select bleed method" });
    }
  });

  app.post('/api/jobs/:id/ai-enhance', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const { enhancement, enabled, options } = req.body;

      const validEnhancements = ["denoise", "sharpen_logos", "spell_check", "tac_limit", "trapping", "engagement_score", "background_remove", "text_reconstruct", "expand_background", "identify_fonts", "test_design_style"];
      if (typeof enhancement !== "string" || !validEnhancements.includes(enhancement)) {
        return res.status(400).json({ message: `Invalid enhancement: ${enhancement}. Valid: ${validEnhancements.join(", ")}` });
      }
      if (typeof enabled !== "boolean") {
        return res.status(400).json({ message: "Field 'enabled' must be a boolean" });
      }
      if (isNaN(jobId) || jobId <= 0) {
        return res.status(400).json({ message: "Invalid job ID" });
      }

      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });

      const auditResults = job.auditResults as AuditResults | null;
      if (!auditResults) return res.status(400).json({ message: "Job has no audit results" });

      console.log(`[AI-ENHANCE] job=${jobId} enhancement="${enhancement}" enabled=${enabled}`);

      if (enabled) {
        nukeRamDisk();
      }

      if (!enabled) {
        const artworkPath = (auditResults as any).preBleedPath || job.correctedPath || job.originalPath;
        const backupPath = artworkPath ? artworkPath + '.flyerz_backup' : null;
        if (backupPath && fsSync.existsSync(backupPath) && artworkPath) {
          try {
            fsSync.copyFileSync(backupPath, artworkPath);
            console.log(`[AI-ENHANCE] Restored original from backup: ${backupPath}`);
          } catch (restoreErr) {
            console.warn(`[AI-ENHANCE] Could not restore backup:`, restoreErr);
          }
        }

        const prevResult = (auditResults as any).aiEnhancements?.[enhancement]?.result;
        if (prevResult?.enhanced_path && prevResult.enhanced_path !== artworkPath) {
          try {
            if (fsSync.existsSync(prevResult.enhanced_path)) {
              fsSync.unlinkSync(prevResult.enhanced_path);
              console.log(`[AI-ENHANCE] Cleaned up enhanced file: ${prevResult.enhanced_path}`);
            }
          } catch { /* already gone */ }
        }

        const updatedResults: AuditResults = {
          ...auditResults,
          aiEnhancements: {
            ...(auditResults as any).aiEnhancements,
            [enhancement]: { enabled: false, result: null },
          },
        };
        await storage.updateJob(jobId, { auditResults: updatedResults });
        return res.json({
          success: true,
          enhancement,
          enabled: false,
          message: `${enhancement} disabled — original artwork restored.`,
          originalPreserved: true,
        });
      }

      const artworkPath = (auditResults as any).preBleedPath || job.correctedPath || job.originalPath;
      if (!artworkPath || !fsSync.existsSync(artworkPath)) {
        return res.status(400).json({ message: "Artwork file not found on disk" });
      }

      const AI_SCRIPT = path.join(process.cwd(), "server", "ai_enhancements.py");
      const optionsJson = JSON.stringify(options || {});

      try {
        const { promisify } = await import('util');
        const { execFile } = await import('child_process');
        const execFileAsync = promisify(execFile);

        const { stdout: result } = await execFileAsync(
          PYTHON_BIN,
          [AI_SCRIPT, enhancement, artworkPath, optionsJson],
          { timeout: 35000, encoding: "utf-8", maxBuffer: 2 * 1024 * 1024 }
        );
        const parsed = JSON.parse((result as string).trim());

        if (parsed.success && parsed.enhanced_path && parsed.enhanced_path !== artworkPath && !parsed.stub) {
          const backupPath = artworkPath + '.flyerz_backup';
          if (!fsSync.existsSync(backupPath)) {
            fsSync.copyFileSync(artworkPath, backupPath);
            console.log(`[AI-ENHANCE] Backed up original: ${artworkPath} -> ${backupPath}`);
          }
          try {
            fsSync.copyFileSync(parsed.enhanced_path, artworkPath);
            console.log(`[AI-ENHANCE] Swapped enhanced result over artwork: ${parsed.enhanced_path} -> ${artworkPath}`);
          } catch (swapErr) {
            console.warn(`[AI-ENHANCE] Could not swap enhanced file:`, swapErr);
          }
        }

        const updatedResults: AuditResults = {
          ...auditResults,
          aiEnhancements: {
            ...(auditResults as any).aiEnhancements,
            [enhancement]: { enabled: true, result: parsed },
          },
        };
        await storage.updateJob(jobId, { auditResults: updatedResults });

        res.json({
          success: true,
          enhancement,
          enabled: true,
          stub: parsed.stub || false,
          message: parsed.message,
          originalPreserved: parsed.original_preserved,
          externalApiReady: parsed.external_api_ready,
        });
      } catch (scriptErr: any) {
        const errMsg = scriptErr.message || String(scriptErr);
        const isTimeout = errMsg.includes("timed out") || errMsg.includes("TIMEOUT") || errMsg.includes("busy");
        console.error(`[AI-ENHANCE] Script error for ${enhancement}:`, errMsg.substring(0, 300));
        res.status(isTimeout ? 408 : 500).json({
          message: isTimeout
            ? "AI service is busy, please try again"
            : `Enhancement "${enhancement}" failed: ${errMsg.substring(0, 200)}`,
        });
      }
    } catch (error) {
      console.error("[AI-ENHANCE] Route error:", error);
      res.status(500).json({ message: "AI enhancement request failed" });
    }
  });

  app.get('/api/jobs/:id/ai-enhance-status', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });

      const auditResults = job.auditResults as any;
      const enhancements = auditResults?.aiEnhancements || {};

      res.json({
        denoise: enhancements.denoise || { enabled: false, result: null },
        sharpen_logos: enhancements.sharpen_logos || { enabled: false, result: null },
        spell_check: enhancements.spell_check || { enabled: false, result: null },
        tac_limit: enhancements.tac_limit || { enabled: false, result: null },
        trapping: enhancements.trapping || { enabled: false, result: null },
        engagement_score: enhancements.engagement_score || { enabled: false, result: null },
        background_remove: enhancements.background_remove || { enabled: false, result: null },
        text_reconstruct: enhancements.text_reconstruct || { enabled: false, result: null },

        expand_background: enhancements.expand_background || { enabled: false, result: null },
        identify_fonts: enhancements.identify_fonts || { enabled: false, result: null },
        test_design_style: enhancements.test_design_style || { enabled: false, result: null },
      });
    } catch (error) {
      console.error("[AI-ENHANCE] Status error:", error);
      res.status(500).json({ message: "Failed to get enhancement status" });
    }
  });

  // --- Optional Text Clear-up (OCR → edit → overlay). Unused = zero pipeline change. ---
  const resolveTextClearupArtwork = (job: any, auditResults: any): string | null => {
    const p = auditResults?.preBleedPath || job.correctedPath || job.originalPath;
    if (!p || !fsSync.existsSync(p)) return null;
    return p;
  };

  const runTextClearupScript = async (action: string, artworkPath: string, extraArg?: string) => {
    const { promisify } = await import("util");
    const { execFile } = await import("child_process");
    const execFileAsync = promisify(execFile);
    const script = path.join(process.cwd(), "server", "text_clearup.py");
    const args = [script, action, artworkPath];
    if (extraArg !== undefined) args.push(extraArg);
    const { stdout } = await execFileAsync(PYTHON_BIN, args, {
      timeout: action === "ocr" || action === "apply" ? 120000 : 90000,
      encoding: "utf-8",
      maxBuffer: 8 * 1024 * 1024,
      env: PYTHON_ENV,
      cwd: process.cwd(),
    });
    return JSON.parse((stdout as string).trim());
  };

  app.post("/api/jobs/:id/text-clearup/detect", async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });
      const auditResults = job.auditResults as AuditResults | null;
      if (!auditResults) return res.status(400).json({ message: "Job has no audit results" });

      const artworkPath = resolveTextClearupArtwork(job, auditResults);
      if (!artworkPath) {
        return res.json({
          success: true,
          offer_clearup: false,
          blurry_text: false,
          message: "Artwork not ready for text clear-up detection",
        });
      }

      const parsed = await runTextClearupScript("detect", artworkPath);
      const offer = !!(parsed.offer_clearup || parsed.blurry_text);
      const updated: AuditResults = {
        ...auditResults,
        textClearup: {
          ...(auditResults as any).textClearup,
          offer,
          detected: offer,
          reason: parsed.reason || parsed.message,
          applied: (auditResults as any).textClearup?.applied || false,
        },
      };
      await storage.updateJob(jobId, { auditResults: updated });
      res.json({
        success: true,
        offer_clearup: offer,
        blurry_text: offer,
        reason: parsed.reason || parsed.message,
        confidence: parsed.confidence,
        message: parsed.message,
      });
    } catch (error: any) {
      console.error("[TEXT-CLEARUP] detect failed:", error?.message || error);
      // Soft-fail: never block bleed/download if detection fails
      res.json({
        success: false,
        offer_clearup: false,
        blurry_text: false,
        message: "Text clear-up detection unavailable",
      });
    }
  });

  app.post("/api/jobs/:id/text-clearup/ocr", async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });
      const auditResults = job.auditResults as AuditResults | null;
      if (!auditResults) return res.status(400).json({ message: "Job has no audit results" });

      const artworkPath = resolveTextClearupArtwork(job, auditResults);
      if (!artworkPath) return res.status(400).json({ message: "Artwork file not found on disk" });

      const parsed = await runTextClearupScript("ocr", artworkPath);
      if (!parsed.success) {
        return res.status(parsed.message?.includes("GEMINI") ? 503 : 500).json({
          message: parsed.message || "OCR failed",
        });
      }

      const updated: AuditResults = {
        ...auditResults,
        textClearup: {
          ...(auditResults as any).textClearup,
          offer: true,
          detected: true,
          blocks: parsed.blocks || [],
          applied: false,
        },
      };
      await storage.updateJob(jobId, { auditResults: updated });
      res.json({
        success: true,
        blocks: parsed.blocks || [],
        block_count: parsed.block_count || 0,
        message: parsed.message,
      });
    } catch (error: any) {
      console.error("[TEXT-CLEARUP] ocr failed:", error?.message || error);
      const msg = String(error?.message || error);
      const isTimeout = /timed out|timeout|busy/i.test(msg);
      res.status(isTimeout ? 408 : 500).json({
        message: isTimeout ? "OCR is busy, please try again" : `OCR failed: ${msg.substring(0, 200)}`,
      });
    }
  });

  app.post("/api/jobs/:id/text-clearup/apply", async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const blocks = Array.isArray(req.body?.blocks) ? req.body.blocks : [];
      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });
      const auditResults = job.auditResults as AuditResults | null;
      if (!auditResults) return res.status(400).json({ message: "Job has no audit results" });

      const artworkPath = resolveTextClearupArtwork(job, auditResults);
      if (!artworkPath) return res.status(400).json({ message: "Artwork file not found on disk" });
      if (!blocks.length) return res.status(400).json({ message: "No text blocks to apply" });

      const parsed = await runTextClearupScript("apply", artworkPath, JSON.stringify(blocks));
      if (!parsed.success || !parsed.enhanced_path) {
        return res.status(500).json({ message: parsed.message || "Overlay apply failed" });
      }

      const backupPath = artworkPath + ".flyerz_text_clearup_backup";
      if (!fsSync.existsSync(backupPath)) {
        fsSync.copyFileSync(artworkPath, backupPath);
      }
      fsSync.copyFileSync(parsed.enhanced_path, artworkPath);

      const updated: AuditResults = {
        ...auditResults,
        textClearup: {
          ...(auditResults as any).textClearup,
          offer: true,
          detected: true,
          applied: true,
          blocks,
        },
      };
      await storage.updateJob(jobId, { auditResults: updated });

      console.log(`[TEXT-CLEARUP] Applied overlay for job ${jobId} (${parsed.blocks_applied} blocks) → ${artworkPath}`);
      res.json({
        success: true,
        blocks_applied: parsed.blocks_applied,
        message: parsed.message,
        // Client should re-run select-bleed-method to refresh precompile ZIP
        needsRecompile: true,
      });
    } catch (error: any) {
      console.error("[TEXT-CLEARUP] apply failed:", error?.message || error);
      res.status(500).json({ message: `Apply failed: ${String(error?.message || error).substring(0, 200)}` });
    }
  });

  app.post("/api/jobs/:id/text-clearup/skip", async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });
      const auditResults = job.auditResults as AuditResults | null;
      if (!auditResults) return res.status(400).json({ message: "Job has no audit results" });
      const updated: AuditResults = {
        ...auditResults,
        textClearup: {
          ...(auditResults as any).textClearup,
          offer: false,
          skipped: true,
        } as any,
      };
      await storage.updateJob(jobId, { auditResults: updated });
      res.json({ success: true, message: "Text clear-up skipped — existing artwork unchanged" });
    } catch (error) {
      res.status(500).json({ message: "Failed to skip text clear-up" });
    }
  });

  app.post('/api/jobs/:id/compile-print-pdf', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const { selectedStrategy = "auto", exportPreferences = {}, autoShifter = false } = req.body;
      const colorSpace = exportPreferences.colorSpace || "cmyk";
      const trimWidth = exportPreferences.trimWidth || 148;
      const trimHeight = exportPreferences.trimHeight || 210;

      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });
      if (job.status !== "complete") return res.status(400).json({ message: "Job not yet complete" });

      const auditResults = job.auditResults as AuditResults | null;
      const compileSavedOpts = coerceSavedBleedOptionsFromDb((auditResults as any)?.savedBleedOptions);

      const rawCrop = req.body.cropData;
      const requestCrop = rawCrop && typeof rawCrop === 'object' ? {
        cropX: Number(rawCrop.cropX) || 0,
        cropY: Number(rawCrop.cropY) || 0,
        cropWidth: Number(rawCrop.cropWidth) || 0,
        cropHeight: Number(rawCrop.cropHeight) || 0,
      } : null;
      const requestHasCrop = !!(requestCrop && isFinite(requestCrop.cropWidth) && requestCrop.cropWidth > 0 && isFinite(requestCrop.cropHeight) && requestCrop.cropHeight > 0);
      const dbHasCrop = !!(compileSavedOpts?.cropWidth && compileSavedOpts?.cropHeight);
      const hasCrop = requestHasCrop || dbHasCrop;

      const cropSource = requestHasCrop ? requestCrop : (dbHasCrop ? compileSavedOpts : null);

      console.log(`[FAI] compile-print-pdf crop resolution: requestHasCrop=${requestHasCrop} dbHasCrop=${dbHasCrop} hasCrop=${hasCrop} source=${requestHasCrop ? 'POST_BODY' : dbHasCrop ? 'DB' : 'NONE'}`);
      if (hasCrop && cropSource) {
        console.log(`[FAI] Crop values: x=${cropSource.cropX}, y=${cropSource.cropY}, w=${cropSource.cropWidth}, h=${cropSource.cropHeight}`);
      }

      const preBleedPath = (auditResults as any)?.preBleedPath;
      let artworkPath = hasCrop ? job.originalPath : (preBleedPath || job.originalPath);
      if (preBleedPath && !hasCrop) {
        try { await fs.access(preBleedPath); } catch {
          console.log(`[FAI] compile-print-pdf: preBleedPath missing (${preBleedPath}), falling back to originalPath`);
          artworkPath = job.originalPath;
        }
      }
      if (!artworkPath || !isPathSafe(artworkPath)) {
        return res.status(400).json({ message: "No artwork available for compilation" });
      }
      try { await fs.access(artworkPath); } catch {
        return res.status(400).json({ message: "Artwork file not found on disk" });
      }

      console.log(`DEBUG: Compiling PDF for Job #${jobId}. Source: ${artworkPath}, Manual Crop Active: ${hasCrop}, preBleedPath: ${preBleedPath || 'NONE'}`);

      const effectiveStrategy = selectedStrategy;

      const cropArgs = (hasCrop && cropSource) ? [
        "--crop-x", String(cropSource.cropX || 0),
        "--crop-y", String(cropSource.cropY || 0),
        "--crop-w", String(cropSource.cropWidth),
        "--crop-h", String(cropSource.cropHeight)
      ] : [];

      console.log(`TRACER: [Checkpoint C] compile-print-pdf: job=${jobId} requestBody.selectedStrategy="${selectedStrategy}" effectiveStrategy="${effectiveStrategy}" dbSelectedBleedMethod="${auditResults?.selectedBleedMethod || 'none'}"`);
      console.log(`[FAI] Compile strategy: direct="${effectiveStrategy}" (no DB fallback)`);

      const { inputPath: resolvedInput, tempSymlink } = ensureExtensionPath(artworkPath, job.filename, jobId);

      const baseName = path.parse(job.filename).name;
      const zipPath = path.join(uploadDir, `flyerz_precompile_${jobId}.zip`);
      const proofPath = auditResults?.bleedProofPath || auditResults?.proofPath || "";
      const reportPath = auditResults?.healthReportPath || "";

      const task = createTask(jobId);
      const outputPath = path.join("uploads", `${jobId}_press_ready_${task.taskId.slice(0, 8)}.pdf`);
      const statusFile = path.join(os.tmpdir(), `compile_status_${task.taskId}.json`);
      const resultFile = path.join(os.tmpdir(), `compile_result_${task.taskId}.json`);

      // Same as precompile: never pass creep for standard flyer press-ready output (see _apply_creep_shift in compile_press_pdf.py).
      const creepMm = 0;

      const args = [
        COMPILE_SCRIPT,
        "--input", resolvedInput,
        "--output", outputPath,
        "--strategy", effectiveStrategy,
        "--color-space", colorSpace,
        "--trim-w", String(trimWidth),
        "--trim-h", String(trimHeight),
        "--status-file", statusFile,
        "--result-file", resultFile,
        "--zip-output", zipPath,
        "--proof-path", proofPath,
        "--report-path", reportPath,
        "--base-name", baseName,
        "--creep-mm", String(creepMm),
        ...cropArgs,
        ...(autoShifter ? ["--auto-shifter", "2.0"] : []),
      ];

      if (effectiveStrategy === "colorBorder") {
        const bodyColor = req.body?.bleedBorderColor;
        const colorHex = sanitizeBleedBorderColor(
          bodyColor != null && String(bodyColor).trim() !== ""
            ? bodyColor
            : compileSavedOpts?.bleedBorderColor
        );
        args.push("--bleed-color", colorHex);
      }

      if (job.originalPath && job.originalPath !== artworkPath) {
        args.push("--original-path", job.originalPath);
      }

      const child = spawn(PYTHON_BIN, args, {
        cwd: process.cwd(),
        env: PYTHON_ENV,
        stdio: ["pipe", "pipe", "pipe"],
      });

      child.stdout.on("data", (data: Buffer) => {
        const lines = data.toString().trim();
        if (lines) console.log(`[COMPILE-STDOUT job=${jobId}] ${lines}`);
      });
      child.stderr.on("data", (data: Buffer) => {
        const lines = data.toString().trim();
        if (lines) console.error(`[COMPILE-STDERR job=${jobId}] ${lines}`);
      });

      let pollInterval: ReturnType<typeof setInterval> | null = null;

      const compileTimeout = setTimeout(() => {
        child.kill("SIGTERM");
        updateTask(task.taskId, { state: "FAILURE", message: "Compilation timed out after 180 seconds" });
        if (pollInterval) clearInterval(pollInterval);
      }, COMPILE_TIMEOUT_MS);

      pollInterval = setInterval(() => {
        try {
          if (fsSync.existsSync(statusFile)) {
            const raw = fsSync.readFileSync(statusFile, "utf-8");
            const status = JSON.parse(raw);
            updateTask(task.taskId, { state: status.state, message: status.message });
          }
        } catch {}
      }, 500);

      child.on("close", async (code) => {
        clearTimeout(compileTimeout);
        if (pollInterval) clearInterval(pollInterval);

        try {
          if (fsSync.existsSync(resultFile)) {
            const raw = fsSync.readFileSync(resultFile, "utf-8");
            const result = JSON.parse(raw);
            if (result.success) {
              updateTask(task.taskId, {
                state: "COMPLETE",
                message: "Press-ready PDF compiled successfully.",
                outputPath: result.outputPath,
                downloadUrl: `/api/jobs/${jobId}/download-bundle?strategy=${encodeURIComponent(effectiveStrategy)}`,
                auditReport: result.audit_report || undefined,
                glitchyMessage: result.glitchy_message || undefined,
                glitchyState: result.glitchy_state || undefined,
              });

              const freshJob = await storage.getJob(jobId);
              const freshAudit = (freshJob?.auditResults || auditResults || { checks: [], overallPassed: false, fixesApplied: 0, complianceReport: "" }) as AuditResults;
              const updatedResults: AuditResults = {
                ...freshAudit,
                compiledPdfPath: result.outputPath,
                compileTaskId: task.taskId,
                compiledStrategy: effectiveStrategy,
                compileAuditReport: result.audit_report || undefined,
              };
              await storage.updateJob(jobId, { auditResults: updatedResults });

              if (fsSync.existsSync(zipPath)) {
                const oldEntry = preCompileCache.get(jobId);
                if (oldEntry && oldEntry.state === "compiling" && oldEntry.childPid) {
                  try { process.kill(oldEntry.childPid, "SIGTERM"); } catch {}
                }
                if (oldEntry?.resolveDone) oldEntry.resolveDone();
                preCompileCache.delete(jobId);
                const entry: PreCompileEntry = {
                  state: "ready",
                  strategy: effectiveStrategy,
                  zipPath,
                  taskId: task.taskId,
                  startedAt: Date.now(),
                  auditReport: result.audit_report,
                  glitchyMessage: result.glitchy_message,
                  glitchyState: result.glitchy_state,
                };
                preCompileCache.set(jobId, entry);
                console.log(`[FAI] compile-print-pdf updated preCompileCache for job ${jobId} (strategy: ${effectiveStrategy}, zip: ${zipPath})`);
              }
            } else {
              updateTask(task.taskId, { state: "FAILURE", message: result.error || "Compilation failed", error: result.error });
            }
          } else {
            updateTask(task.taskId, { state: "FAILURE", message: `Process exited with code ${code}` });
          }
        } catch (err: any) {
          updateTask(task.taskId, { state: "FAILURE", message: err.message || "Unknown error" });
        }

        try { await fs.unlink(statusFile); } catch {}
        try { await fs.unlink(resultFile); } catch {}
        const curEntry = preCompileCache.get(jobId);
        if (tempSymlink && (!curEntry || curEntry.taskId === task.taskId)) {
          try { await fs.unlink(tempSymlink); } catch {}
        }
      });

      console.log(`[FAI] Compile task ${task.taskId} started for job ${jobId} (strategy: ${effectiveStrategy} [direct param], color: ${colorSpace})`);
      res.status(202).json({ taskId: task.taskId });
    } catch (error: any) {
      console.error("[FAI] Compile press-ready PDF failed:", error);
      res.status(500).json({ message: "Failed to start compilation" });
    }
  });

  app.get('/api/jobs/:id/compile-status/:taskId', async (req, res) => {
    try {
      const taskId = req.params.taskId;
      const task = getTask(taskId);
      if (!task) return res.status(404).json({ state: "NOT_FOUND", message: "Task not found" });

      const response: Record<string, any> = {
        state: task.state,
        message: task.message,
      };
      if (task.downloadUrl) response.downloadUrl = task.downloadUrl;
      if (task.error) response.error = task.error;
      if (task.auditReport) response.audit_report = task.auditReport;
      if (task.glitchyMessage) response.glitchy_message = task.glitchyMessage;
      if (task.glitchyState) response.glitchy_state = task.glitchyState;

      res.json(response);
    } catch (error) {
      res.status(500).json({ state: "ERROR", message: "Failed to get task status" });
    }
  });

  app.get('/api/jobs/:id/download/press-ready', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const taskId = req.query.taskId as string;

      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });

      const auditResults = job.auditResults as AuditResults | null;
      const compiledPath = auditResults?.compiledPdfPath;

      if (!compiledPath || !isPathSafe(compiledPath)) {
        return res.status(404).json({ message: "No compiled PDF available" });
      }

      try { await fs.access(compiledPath); } catch {
        return res.status(404).json({ message: "Compiled PDF file not found on disk" });
      }

      const downloadName = `Print Ready Artwork.pdf`;
      res.download(compiledPath, downloadName);
    } catch (error) {
      console.error("[FAI] Download press-ready failed:", error);
      res.status(500).json({ message: "Failed to download press-ready PDF" });
    }
  });

  app.get('/api/jobs/:id/precompile-status', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const requestedStrategy = (req.query.strategy as string) || "";
      const entry = preCompileCache.get(jobId);

      if (entry && entry.state === "compiling") {
        const elapsed = Date.now() - entry.startedAt;
        const MAX_COMPILE_MS = 5 * 60 * 1000;
        if (elapsed > MAX_COMPILE_MS) {
          const isAlive = entry.childPid ? (() => { try { process.kill(entry.childPid!, 0); return true; } catch { return false; } })() : false;
          if (!isAlive) {
            console.log(`[FAI] Pre-compile STALE for job ${jobId}: started ${Math.round(elapsed / 1000)}s ago, process dead — marking failed`);
            entry.state = "failed";
            entry.error = "Compilation timed out or process crashed. Please try again.";
            if (entry.resolveDone) entry.resolveDone();
          }
        }
      }

      if (!entry) {
        const zipPath = path.join(uploadDir, `flyerz_precompile_${jobId}.zip`);
        try {
          await fs.access(zipPath);
          const recoveryJob = await storage.getJob(jobId);
          const recoveryAudit = recoveryJob?.auditResults as AuditResults | null;
          const compiledStrat = recoveryAudit?.compiledStrategy;
          if (!compiledStrat || (requestedStrategy && compiledStrat !== requestedStrategy)) {
            console.log(`[FAI] precompile-status cache-miss recovery: ZIP exists but compiledStrategy="${compiledStrat || 'MISSING'}" vs requested="${requestedStrategy}" — rejecting unknown/stale ZIP`);
            try { await fs.unlink(zipPath); } catch {}
            return res.json({ state: "none", message: `Orphan ZIP rejected (strategy unverifiable). Recompile needed.` });
          }
          console.log(`[FAI] precompile-status cache-miss recovery: ZIP exists, compiledStrategy="${compiledStrat}" matches requested="${requestedStrategy}" — serving`);
          preCompileCache.set(jobId, { strategy: compiledStrat, state: "ready", startedAt: Date.now(), taskId: "", zipPath });
          return res.json({ state: "ready", message: "Pre-compilation ready (recovered from cache miss)", strategy: compiledStrat });
        } catch {}
        return res.json({ state: "none", message: "No pre-compilation in progress" });
      }

      if (requestedStrategy && entry.strategy !== requestedStrategy) {
        return res.json({ state: "none", message: `Pre-compilation is for "${entry.strategy}", not "${requestedStrategy}"` });
      }

      const task = getTask(entry.taskId);
      res.json({
        state: entry.state,
        strategy: entry.strategy,
        taskId: entry.taskId,
        message: task?.message || "",
        error: entry.error,
        auditReport: entry.auditReport,
        glitchyMessage: entry.glitchyMessage,
        glitchyState: entry.glitchyState,
      });
    } catch (error) {
      res.status(500).json({ state: "error", message: "Failed to get pre-compile status" });
    }
  });

  app.get('/api/jobs/:id/download-bundle', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const requestedStrategy = (req.query.strategy as string) || "";
      console.log(`TRACER: [Checkpoint B] Backend received strategy = '${requestedStrategy}' for job ${jobId} — raw query: ${JSON.stringify(req.query)}`);

      if (!requestedStrategy) {
        console.log(`[FAI] download-bundle called WITHOUT strategy param for job ${jobId} — rejecting`);
        return res.status(400).json({ message: "Missing strategy query parameter. Pass ?strategy=<name>" });
      }

      const job = await storage.getJob(jobId);
      if (!job) return res.status(404).json({ message: "Job not found" });

      const auditResults = job.auditResults as AuditResults | null;
      let entry = preCompileCache.get(jobId);
      const zipPath = path.join(uploadDir, `flyerz_precompile_${jobId}.zip`);

      if (entry && entry.strategy !== requestedStrategy) {
        console.log(`[FAI] download-bundle: strategy mismatch — cached="${entry.strategy}" vs requested="${requestedStrategy}" — annihilating stale cache`);
        cancelPreCompile(jobId);
        entry = undefined;
      }

      console.log(`TRACER: [Checkpoint D] download-bundle: job=${jobId} requested="${requestedStrategy}" cached="${entry?.strategy || 'none'}" cacheState="${entry?.state || 'none'}" dbCompiledStrategy="${auditResults?.compiledStrategy || 'none'}" dbSelectedBleedMethod="${auditResults?.selectedBleedMethod || 'none'}"`);

      if (entry?.state === "compiling" && entry.donePromise) {
        console.log(`[FAI] download-bundle: AWAITING compile completion for job ${jobId} (strategy: ${requestedStrategy})...`);
        const timeout = new Promise<"timeout">((resolve) => setTimeout(() => resolve("timeout"), COMPILE_TIMEOUT_MS + 5000));
        const race = await Promise.race([entry.donePromise.then(() => "done" as const), timeout]);
        if (race === "timeout") {
          console.log(`[FAI] download-bundle: timed out waiting for compile for job ${jobId}`);
          return res.status(504).json({ message: "Compilation timed out. Please try again." });
        }
        entry = preCompileCache.get(jobId);
        console.log(`[FAI] download-bundle: compile finished for job ${jobId}, state=${entry?.state}`);
      }

      if (entry?.state === "ready") {
        if (!fsSync.existsSync(zipPath)) {
          console.error(`[FAI] download-bundle: ZIP missing at ${zipPath} despite cache state=ready — 500`);
          return res.status(500).json({ message: "Compiled ZIP file is missing. Please recompile." });
        }
        let stat: ReturnType<typeof fsSync.statSync>;
        try { stat = fsSync.statSync(zipPath); } catch (e: any) {
          console.error(`[FAI] download-bundle: statSync failed for ${zipPath}: ${e.message}`);
          return res.status(500).json({ message: "Cannot read compiled ZIP. Please recompile." });
        }
        if (stat.size === 0) {
          console.error(`[FAI] download-bundle: ZIP at ${zipPath} is 0 bytes — 500`);
          try { fsSync.unlinkSync(zipPath); } catch {}
          entry.state = "failed";
          entry.error = "Generated ZIP was empty";
          return res.status(500).json({ message: "Compiled ZIP is empty. Please recompile." });
        }
        res.setHeader("Content-Type", "application/zip");
        res.setHeader("Content-Length", stat.size);
        res.setHeader("Content-Disposition", `attachment; filename="Print Ready Artwork.zip"`);
        const stream = fsSync.createReadStream(zipPath);
        stream.pipe(res);
        console.log(`[FAI] Served cached ZIP for job ${jobId} (${stat.size} bytes, strategy: ${requestedStrategy})`);
        return;
      }

      if (entry?.state === "failed") {
        const errMsg = entry.error || "Compilation failed";
        console.log(`[FAI] download-bundle: compile FAILED for job ${jobId}: ${errMsg}`);
        return res.status(500).json({ message: `Compilation failed: ${errMsg}. Please try again.` });
      }

      if (auditResults?.compiledStrategy && auditResults.compiledStrategy !== requestedStrategy) {
        console.log(`[FAI] Compiled PDF strategy="${auditResults.compiledStrategy}" doesn't match requested="${requestedStrategy}" — rejecting stale PDF`);
        return res.status(404).json({ message: `No compiled PDF for strategy "${requestedStrategy}". Please compile first.` });
      }

      if (fsSync.existsSync(zipPath)) {
        const freshAuditForRecovery = ((await storage.getJob(jobId))?.auditResults as AuditResults) || null;
        const recoveredStrat = freshAuditForRecovery?.compiledStrategy;
        if (!recoveredStrat || recoveredStrat !== requestedStrategy) {
          console.log(`[FAI] download-bundle orphan ZIP: compiledStrategy="${recoveredStrat || 'MISSING'}" vs requested="${requestedStrategy}" — deleting unverifiable/stale ZIP`);
          try { fsSync.unlinkSync(zipPath); } catch {}
        } else {
          const stat = fsSync.statSync(zipPath);
          if (stat.size > 0) {
            res.setHeader("Content-Type", "application/zip");
            res.setHeader("Content-Length", stat.size);
            res.setHeader("Content-Disposition", `attachment; filename="Print Ready Artwork.zip"`);
            const stream = fsSync.createReadStream(zipPath);
            stream.pipe(res);
            console.log(`[FAI] Served recovered ZIP for job ${jobId} (${stat.size} bytes, strategy: ${requestedStrategy}, verified="${recoveredStrat}")`);
            preCompileCache.set(jobId, { strategy: recoveredStrat, state: "ready", startedAt: Date.now(), taskId: "", zipPath });
            return;
          }
        }
      }

      const compiledPath = auditResults?.compiledPdfPath;
      if (!compiledPath || !isPathSafe(compiledPath)) {
        return res.status(404).json({ message: "No compiled PDF available. Please compile first." });
      }
      try { await fs.access(compiledPath); } catch {
        return res.status(404).json({ message: "Compiled PDF file not found on disk" });
      }

      return res.status(404).json({ message: "Press-ready ZIP not found. Please recompile by selecting a bleed strategy." });
    } catch (error) {
      console.error("[FAI] Download bundle failed:", error);
      if (!res.headersSent) res.status(500).json({ message: "Failed to create download bundle" });
    }
  });

  cleanStaleTasks();
  setInterval(cleanStaleTasks, 5 * 60 * 1000);

  const CHECKS_GUIDE_SCRIPT = path.join(process.cwd(), "server", "checks_guide.py");
  const CHECKS_GUIDE_CACHE = path.join(os.tmpdir(), "flyerz_system_intelligence_guide.pdf");
  /** Legacy temp PDF from older builds — delete so nothing ever re-streams a white “check guide”. */
  const LEGACY_CHECKS_GUIDE_CACHES = [
    path.join(os.tmpdir(), "flyerz_checks_guide.pdf"),
    path.join(os.tmpdir(), "flyerz_25_point_check_guide.pdf"),
  ];

  /** Static System Intelligence Guide only — use GET /api/jobs/:id/intelligence-report for job telemetry PDFs. */
  app.get('/api/checks-guide', async (req, res) => {
    try {
      const downloadName = 'Flyerz_System_Intelligence_Guide.pdf';

      for (const stale of LEGACY_CHECKS_GUIDE_CACHES) {
        try {
          await fs.unlink(stale);
        } catch {
          /* ignore */
        }
      }

      execPythonCapture([CHECKS_GUIDE_SCRIPT, CHECKS_GUIDE_CACHE], 'ChecksGuide');

      res.setHeader('Content-Type', 'application/pdf');
      res.setHeader('Content-Disposition', `attachment; filename="${downloadName}"`);
      res.setHeader('Cache-Control', 'private, no-store, must-revalidate');
      res.setHeader('Pragma', 'no-cache');
      res.setHeader('Expires', '0');
      const { createReadStream } = await import('fs');
      createReadStream(CHECKS_GUIDE_CACHE).pipe(res);
    } catch (error) {
      console.error('[FAI] Checks guide generation failed:', error);
      res.status(500).json({ message: 'Failed to generate checks guide' });
    }
  });

  // Delete job
  app.delete(api.jobs.delete.path, async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const job = await storage.getJob(jobId);
      
      if (!job) {
        return res.status(404).json({ message: 'Job not found' });
      }

      // Delete files
      try {
        await fs.unlink(job.originalPath);
      } catch (error) {
        console.error('Error deleting original file:', error);
      }

      if (job.correctedPath) {
        try {
          await fs.unlink(job.correctedPath);
        } catch (error) {
          console.error('Error deleting corrected file:', error);
        }
      }

      await storage.deleteJob(jobId);
      res.status(204).send();
    } catch (error) {
      console.error('Error deleting job:', error);
      res.status(500).json({ message: 'Failed to delete job' });
    }
  });

  // ── Manual Crop & Downscale ──
  const CROP_SCRIPT = path.join(process.cwd(), "server", "manual_crop.py");
  const cropDir = path.join(process.cwd(), "uploads", "cropped");

  async function ensureCropDir() {
    try {
      await fs.access(cropDir);
    } catch {
      await fs.mkdir(cropDir, { recursive: true });
    }
  }

  await ensureCropDir();

  // Upload file and get preview for crop selection
  app.post(api.manualCrop.preview.path, upload.single('file'), async (req, res) => {
    try {
      if (!req.file) {
        return res.status(400).json({ message: 'No file uploaded' });
      }

      const ext = path.extname(req.file.originalname).toLowerCase();
      const fileType = ext.replace('.', '');
      const allowedTypes = ['pdf', 'jpg', 'jpeg', 'png'];
      if (!allowedTypes.includes(fileType)) {
        return res.status(400).json({ message: 'Only PDF, JPG, and PNG files are supported for cropping.' });
      }

      const previewFilename = `${Date.now()}_preview.png`;
      const previewPath = path.join(cropDir, previewFilename);

      // Store original file reference for later crop
      const origFilename = `${Date.now()}_${req.file.originalname}`;
      const origStorePath = path.join(cropDir, origFilename);
      await fs.rename(req.file.path, origStorePath);

      const result = execPythonCapture([
        CROP_SCRIPT, origStorePath, previewPath,
        fileType === 'jpeg' ? 'jpg' : fileType, "preview"
      ], "CropPreview");

      res.json({
        ...result,
        originalFilename: req.file.originalname,
        storedFilename: origFilename,
        previewFilename,
        fileType: fileType === 'jpeg' ? 'jpg' : fileType,
      });
    } catch (error) {
      console.error('Error generating crop preview:', error);
      res.status(500).json({ message: error instanceof Error ? error.message : 'Failed to generate preview' });
    }
  });

  // Execute crop + downscale
  app.post(api.manualCrop.execute.path, async (req, res) => {
    try {
      const { storedFilename, fileType, cropX, cropY, cropWidth, cropHeight, scalePercent } = req.body;

      if (!storedFilename || !fileType) {
        return res.status(400).json({ message: 'Missing required fields: storedFilename, fileType' });
      }

      const x = parseFloat(cropX) || 0;
      const y = parseFloat(cropY) || 0;
      const w = parseFloat(cropWidth);
      const h = parseFloat(cropHeight);
      const scale = parseFloat(scalePercent) || 100;

      if (!w || !h || w <= 0 || h <= 0) {
        return res.status(400).json({ message: 'Invalid crop dimensions. Width and height must be positive.' });
      }

      if (scale < 10 || scale > 100) {
        return res.status(400).json({ message: 'Scale percentage must be between 10% and 100%.' });
      }

      const inputPath = path.join(cropDir, storedFilename);
      try {
        await fs.access(inputPath);
      } catch {
        return res.status(404).json({ message: 'Source file not found. Please re-upload.' });
      }

      const ext = fileType === 'pdf' ? '.pdf' : fileType === 'png' ? '.png' : '.jpg';
      const basename = path.basename(storedFilename, path.extname(storedFilename))
        .replace(/^\d+_/, '');
      const outputFilename = `${Date.now()}_${basename}_cropped${ext}`;
      const outputPath = path.join(cropDir, outputFilename);

      const result = execPythonCapture([
        CROP_SCRIPT, inputPath, outputPath, fileType, "crop",
        String(x), String(y), String(w), String(h), String(scale)
      ], "CropExecute");

      const friendlyName = `${basename}_cropped_${scale}pct${ext}`;
      res.json({
        ...result,
        downloadUrl: `/api/manual-crop/download/${outputFilename}`,
        downloadFilename: friendlyName,
      });
    } catch (error) {
      console.error('Error executing crop:', error);
      res.status(500).json({ message: error instanceof Error ? error.message : 'Failed to crop file' });
    }
  });

  // Serve crop preview images
  app.get('/api/manual-crop/preview-image/:filename', async (req, res) => {
    try {
      const filename = req.params.filename;
      if (filename.includes('..') || filename.includes('/')) {
        return res.status(400).json({ message: 'Invalid filename' });
      }
      const filePath = path.join(cropDir, filename);
      try {
        await fs.access(filePath);
      } catch {
        return res.status(404).json({ message: 'Preview file not found' });
      }
      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'no-cache');
      const stream = (await import('fs')).createReadStream(filePath);
      stream.pipe(res);
    } catch (error) {
      res.status(500).json({ message: 'Failed to serve preview' });
    }
  });

  app.post('/api/preview-pdf-page', upload.single('file'), async (req, res) => {
    try {
      if (!req.file) return res.status(400).json({ message: 'No file uploaded' });
      const tmpInput = req.file.path;
      const ext = path.extname(req.file.originalname || "").toLowerCase();
      const previewFilename = `file_preview_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.png`;
      const previewPath = path.join(cropDir, previewFilename);

      const py = spawn(PYTHON_BIN, ["-c", `
import sys, os
input_path = sys.argv[1]
output_path = sys.argv[2]
ext = sys.argv[3].lower()

PDF_EXTS = {'.pdf'}
FITZ_EXTS = {'.pdf', '.xps', '.epub', '.mobi', '.fb2', '.cbz', '.svg'}
PIL_EXTS = {'.tif', '.tiff', '.eps', '.bmp', '.webp', '.ico', '.psd', '.dds', '.pcx', '.ppm', '.pgm', '.pbm', '.tga'}

w, h = 0, 0

if ext in FITZ_EXTS:
    import fitz, cv2, numpy as np
    doc = fitz.open(input_path)
    page = doc[0]
    zoom = max(1.0, min(3.0, 2400.0 / max(page.rect.width, page.rect.height)))
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    cv2.imwrite(output_path, img_bgr)
    w, h = pix.w, pix.h
elif ext in PIL_EXTS:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 300_000_000
    im = Image.open(input_path)
    im.load()
    if im.mode in ('CMYK', 'RGBA', 'LA', 'P'):
        im = im.convert('RGB')
    max_dim = max(im.size)
    if max_dim > 2400:
        scale = 2400.0 / max_dim
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    im.save(output_path, 'PNG')
    w, h = im.size
else:
    try:
        import fitz
        doc = fitz.open(input_path)
        page = doc[0]
        zoom = max(1.0, min(3.0, 2400.0 / max(page.rect.width, page.rect.height)))
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        import cv2, numpy as np
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        cv2.imwrite(output_path, img_bgr)
        w, h = pix.w, pix.h
    except Exception:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = 300_000_000
        im = Image.open(input_path)
        im.load()
        if im.mode in ('CMYK', 'RGBA', 'LA', 'P'):
            im = im.convert('RGB')
        max_dim = max(im.size)
        if max_dim > 2400:
            scale = 2400.0 / max_dim
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        im.save(output_path, 'PNG')
        w, h = im.size

print(f'{w},{h}')
`, tmpInput, previewPath, ext], {
        cwd: process.cwd(),
        env: PYTHON_ENV,
        stdio: ["pipe", "pipe", "pipe"],
      });

      let stdout = "";
      py.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
      let stderr = "";
      py.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });

      py.on("close", async (code) => {
        try { await fs.unlink(tmpInput); } catch {}
        if (code !== 0) {
          console.error(`[FAI] File preview failed: ${stderr}`);
          return res.status(500).json({ message: "Failed to rasterize file for preview" });
        }
        const dims = stdout.trim().split(",");
        res.json({
          previewUrl: `/api/manual-crop/preview-image/${previewFilename}`,
          width: parseInt(dims[0]) || 0,
          height: parseInt(dims[1]) || 0,
        });
      });
    } catch (error) {
      res.status(500).json({ message: 'Failed to generate file preview' });
    }
  });

  // Download cropped file
  app.get('/api/manual-crop/download/:filename', async (req, res) => {
    try {
      const filename = req.params.filename;
      if (filename.includes('..') || filename.includes('/')) {
        return res.status(400).json({ message: 'Invalid filename' });
      }
      const filePath = path.join(cropDir, filename);
      try {
        await fs.access(filePath);
      } catch {
        return res.status(404).json({ message: 'Cropped file not found' });
      }
      res.download(filePath, filename.replace(/^\d+_/, ''));
    } catch (error) {
      console.error('Error downloading cropped file:', error);
      res.status(500).json({ message: 'Failed to download cropped file' });
    }
  });

  // Precision Resizer - upload and resize
  const RESIZE_SCRIPT = path.join(process.cwd(), "server", "precision_resize.py");
  const resizeDir = path.join(process.cwd(), "uploads", "resized");

  async function ensureResizeDir() {
    try {
      await fs.access(resizeDir);
    } catch {
      await fs.mkdir(resizeDir, { recursive: true });
    }
  }

  await ensureResizeDir();

  app.post(api.resize.execute.path, upload.single('file'), async (req, res) => {
    try {
      if (!req.file) {
        return res.status(400).json({ message: 'No file uploaded' });
      }

      const targetWidth = parseFloat(req.body.targetWidth);
      const targetHeight = parseFloat(req.body.targetHeight);
      const uniform = req.body.uniform === '1' || req.body.uniform === 'true';

      if (!targetWidth || !targetHeight || targetWidth <= 0 || targetHeight <= 0) {
        return res.status(400).json({ message: 'Invalid target dimensions. Provide positive width and height in mm.' });
      }

      if (targetWidth > 3000 || targetHeight > 3000) {
        return res.status(400).json({ message: 'Target dimensions exceed maximum of 3000mm.' });
      }

      const ext = path.extname(req.file.originalname).toLowerCase();
      const fileType = ext.replace('.', '');
      const allowedTypes = ['pdf', 'jpg', 'jpeg', 'png'];
      if (!allowedTypes.includes(fileType)) {
        return res.status(400).json({ message: 'Only PDF, JPG, and PNG files can be resized.' });
      }

      const basename = path.basename(req.file.originalname, ext);
      const outputFilename = `${basename}_resized_${targetWidth}x${targetHeight}mm${ext}`;
      const outputPath = path.join(resizeDir, `${Date.now()}_${outputFilename}`);

      const result = execPythonCapture([
        RESIZE_SCRIPT, req.file!.path, outputPath,
        fileType === 'jpeg' ? 'jpg' : fileType,
        String(targetWidth), String(targetHeight), uniform ? '1' : '0'
      ], "Resize");

      // Clean up the uploaded temp file
      try { await fs.unlink(req.file.path); } catch {}

      const downloadFilename = path.basename(outputPath);
      res.json({
        ...result,
        downloadUrl: `/api/resize/download/${downloadFilename}`,
        downloadFilename: outputFilename,
      });
    } catch (error) {
      console.error('Error resizing file:', error);
      res.status(500).json({ message: error instanceof Error ? error.message : 'Failed to resize file' });
    }
  });

  // Download resized file
  app.get('/api/resize/download/:filename', async (req, res) => {
    try {
      const filename = req.params.filename;
      if (filename.includes('..') || filename.includes('/')) {
        return res.status(400).json({ message: 'Invalid filename' });
      }
      const filePath = path.join(resizeDir, filename);
      try {
        await fs.access(filePath);
      } catch {
        return res.status(404).json({ message: 'Resized file not found' });
      }
      res.download(filePath, filename.replace(/^\d+_/, ''));
    } catch (error) {
      console.error('Error downloading resized file:', error);
      res.status(500).json({ message: 'Failed to download resized file' });
    }
  });

  // ─── Safe Margin Shrink Tool ───
  const SHRINK_SCRIPT = path.join(process.cwd(), "server", "safe_margin_shrink.py");
  const shrinkDir = path.join(process.cwd(), "uploads", "shrink");
  try {
    await fs.access(shrinkDir);
  } catch {
    await fs.mkdir(shrinkDir, { recursive: true });
  }

  app.post('/api/shrink/preview', upload.single('file'), async (req, res) => {
    try {
      if (!req.file) {
        return res.status(400).json({ message: 'No file uploaded.' });
      }

      const ext = path.extname(req.file.originalname).toLowerCase();
      if (!['.pdf', '.jpg', '.jpeg', '.png'].includes(ext)) {
        return res.status(400).json({ message: 'Only PDF, JPG, and PNG files are supported.' });
      }

      const shrinkFactor = Math.max(0.50, Math.min(0.99, parseFloat(req.body.shrinkFactor) || 0.92));
      const storedFilename = `${Date.now()}_${req.file.originalname}`;
      const storedPath = path.join(shrinkDir, storedFilename);
      await fs.rename(req.file.path, storedPath);

      const result = execPythonCapture([
        SHRINK_SCRIPT, storedPath, "preview", String(shrinkFactor)
      ], "ShrinkPreview");

      const previewFilename = path.basename(result.previewPath);
      res.json({
        ...result,
        storedFilename,
        fileType: ext.replace('.', ''),
        previewUrl: `/api/shrink/preview-image/${previewFilename}`,
      });
    } catch (error) {
      console.error('Error generating shrink preview:', error);
      res.status(500).json({ message: error instanceof Error ? error.message : 'Failed to generate preview' });
    }
  });

  app.post('/api/shrink/execute', async (req, res) => {
    try {
      const { storedFilename, fileType, shrinkFactor: sf } = req.body;
      if (!storedFilename || !fileType) {
        return res.status(400).json({ message: 'Missing required fields: storedFilename, fileType' });
      }

      const shrinkFactor = Math.max(0.50, Math.min(0.99, parseFloat(sf) || 0.92));
      const inputPath = path.join(shrinkDir, storedFilename);

      try {
        await fs.access(inputPath);
      } catch {
        return res.status(404).json({ message: 'Source file not found. Please re-upload.' });
      }

      const ext = fileType === 'pdf' ? '.pdf' : fileType === 'png' ? '.png' : '.jpg';
      const basename = path.basename(storedFilename, path.extname(storedFilename))
        .replace(/^\d+_/, '');
      const outputFilename = `${Date.now()}_${basename}_safemargin${ext}`;
      const outputPath = path.join(shrinkDir, outputFilename);

      const result = execPythonCapture([
        SHRINK_SCRIPT, inputPath, outputPath, String(shrinkFactor)
      ], "ShrinkExecute");

      const friendlyName = `${basename}_safemargin_${Math.round(shrinkFactor * 100)}pct${ext}`;
      res.json({
        ...result,
        downloadUrl: `/api/shrink/download/${outputFilename}`,
        downloadFilename: friendlyName,
      });
    } catch (error) {
      console.error('Error executing shrink:', error);
      res.status(500).json({ message: error instanceof Error ? error.message : 'Failed to apply safe margin shrink' });
    }
  });

  app.get('/api/shrink/preview-image/:filename', async (req, res) => {
    try {
      const filename = req.params.filename;
      if (filename.includes('..') || filename.includes('/')) {
        return res.status(400).json({ message: 'Invalid filename' });
      }
      const filePath = path.join(shrinkDir, filename);
      try {
        await fs.access(filePath);
      } catch {
        return res.status(404).json({ message: 'Preview not found' });
      }
      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'no-cache');
      const fileBuffer = await fs.readFile(filePath);
      res.send(fileBuffer);
    } catch (error) {
      res.status(500).json({ message: 'Failed to serve preview image' });
    }
  });

  app.get('/api/shrink/download/:filename', async (req, res) => {
    try {
      const filename = req.params.filename;
      if (filename.includes('..') || filename.includes('/')) {
        return res.status(400).json({ message: 'Invalid filename' });
      }
      const filePath = path.join(shrinkDir, filename);
      try {
        await fs.access(filePath);
      } catch {
        return res.status(404).json({ message: 'File not found' });
      }
      const friendlyName = req.query.name || filename;
      res.setHeader('Content-Disposition', `attachment; filename="${friendlyName}"`);
      const fileBuffer = await fs.readFile(filePath);
      res.send(fileBuffer);
    } catch (error) {
      res.status(500).json({ message: 'Failed to download file' });
    }
  });

  app.post('/api/jobs/:id/share', async (req, res) => {
    try {
      const jobId = Number(req.params.id);
      const { email } = req.body;

      if (!email || typeof email !== 'string' || !email.includes('@')) {
        return res.status(400).json({ message: 'A valid email address is required' });
      }

      const job = await storage.getJob(jobId);
      if (!job) {
        return res.status(404).json({ message: 'Job not found' });
      }

      if (job.status !== 'complete') {
        return res.status(400).json({ message: 'Job must be completed before sharing' });
      }

      const audit = job.auditResults as AuditResults | null;
      if (!audit) {
        return res.status(400).json({ message: 'No audit results available' });
      }

      const checks = audit.checks || [];
      const fixedChecks = checks.filter((c: AuditCheck) => c.autoFixed);
      const failedChecks = checks.filter((c: AuditCheck) => !c.passed && !c.autoFixed);
      const passedChecks = checks.filter((c: AuditCheck) => c.passed && !c.autoFixed);

      const summaryLines: string[] = [
        `File: ${job.filename}`,
        `Overall: ${audit.overallPassed ? 'PASSED' : 'NEEDS ATTENTION'}`,
        `Total Checks: ${checks.length}`,
        `Passed: ${passedChecks.length}`,
        `Auto-Fixed: ${fixedChecks.length}`,
        `Failed: ${failedChecks.length}`,
        '',
      ];

      if (fixedChecks.length > 0) {
        summaryLines.push('--- Optimizations Applied ---');
        for (const c of fixedChecks) {
          summaryLines.push(`✔ ${c.name}: ${c.message}`);
        }
        summaryLines.push('');
      }

      if (failedChecks.length > 0) {
        summaryLines.push('--- Issues Remaining ---');
        for (const c of failedChecks) {
          summaryLines.push(`✘ ${c.name}: ${c.message}`);
        }
        summaryLines.push('');
      }

      if (passedChecks.length > 0) {
        summaryLines.push('--- Checks Passed ---');
        for (const c of passedChecks) {
          summaryLines.push(`✔ ${c.name}: ${c.message}`);
        }
      }

      const reportSummary = summaryLines.join('\n');

      const artworkPath = job.correctedPath || job.originalPath;
      const artworkFilename = job.correctedPath
        ? `PrintReady_${job.filename}`
        : job.filename;

      const deploymentUrl = process.env.REPLIT_DOMAINS
        ? `https://${process.env.REPLIT_DOMAINS.split(',')[0]}`
        : process.env.REPL_SLUG
          ? `https://${process.env.REPL_SLUG}.${process.env.REPL_OWNER}.replit.app`
          : 'https://workspace.dwaynesptmygym.replit.app';

      const jobUrl = `${deploymentUrl}/job/${jobId}`;

      const htmlBody = `
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
          <div style="background: linear-gradient(135deg, #7c3aed, #6d28d9); padding: 24px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 22px;">Flyerz.co.za Artwork Intelligence</h1>
            <p style="color: rgba(255,255,255,0.8); margin: 8px 0 0; font-size: 14px;">New Lead — Print Ready Artwork Shared</p>
          </div>
          <div style="background: #f9fafb; padding: 24px; border: 1px solid #e5e7eb;">
            <h2 style="color: #1f2937; font-size: 16px; margin-top: 0;">Customer Details</h2>
            <p style="color: #374151; margin: 4px 0;"><strong>Email:</strong> ${email}</p>
            <p style="color: #374151; margin: 4px 0;"><strong>File:</strong> ${job.filename}</p>
            <p style="color: #374151; margin: 4px 0;"><strong>Job ID:</strong> #${jobId}</p>
            <p style="color: #374151; margin: 4px 0;"><strong>Job Link:</strong> <a href="${jobUrl}" style="color: #7c3aed;">${jobUrl}</a></p>

            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 16px 0;">

            <h2 style="color: #1f2937; font-size: 16px;">Artwork Intelligence Summary</h2>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
              <tr style="background: #f3f4f6;">
                <td style="padding: 8px; border: 1px solid #e5e7eb;"><strong>Total Checks</strong></td>
                <td style="padding: 8px; border: 1px solid #e5e7eb;">${checks.length}</td>
              </tr>
              <tr>
                <td style="padding: 8px; border: 1px solid #e5e7eb;"><strong>Passed</strong></td>
                <td style="padding: 8px; border: 1px solid #e5e7eb; color: #16a34a;">${passedChecks.length}</td>
              </tr>
              <tr style="background: #f3f4f6;">
                <td style="padding: 8px; border: 1px solid #e5e7eb;"><strong>Auto-Fixed</strong></td>
                <td style="padding: 8px; border: 1px solid #e5e7eb; color: #7c3aed;">${fixedChecks.length}</td>
              </tr>
              ${failedChecks.length > 0 ? `<tr>
                <td style="padding: 8px; border: 1px solid #e5e7eb;"><strong>Failed</strong></td>
                <td style="padding: 8px; border: 1px solid #e5e7eb; color: #dc2626;">${failedChecks.length}</td>
              </tr>` : ''}
            </table>

            ${fixedChecks.length > 0 ? `
              <h3 style="color: #7c3aed; font-size: 14px; margin-top: 16px;">Optimizations Applied</h3>
              <ul style="padding-left: 20px; font-size: 13px; color: #374151;">
                ${fixedChecks.map((c: AuditCheck) => `<li style="margin-bottom: 4px;"><strong>${c.name}:</strong> ${c.message}</li>`).join('')}
              </ul>
            ` : ''}
          </div>
          <div style="background: #1f2937; padding: 16px 24px; border-radius: 0 0 12px 12px;">
            <p style="color: rgba(255,255,255,0.6); font-size: 12px; margin: 0; text-align: center;">
              Flyerz.co.za Artwork Intelligence — Automated Pre-press Report
            </p>
          </div>
        </div>
      `;

      let emailSent = false;
      const resendApiKey = process.env.RESEND_API_KEY;

      if (resendApiKey) {
        try {
          const { Resend } = await import('resend');
          const resend = new Resend(resendApiKey);

          const attachments: Array<{ filename: string; content: Buffer }> = [];

          if (artworkPath && isPathSafe(artworkPath)) {
            try {
              const fileBuffer = await fs.readFile(artworkPath);
              attachments.push({ filename: artworkFilename, content: fileBuffer });
            } catch (e) {
              console.warn('[FAI][Share] Could not attach artwork file:', e);
            }
          }

          const healthReportPath = audit.healthReportPath;
          if (healthReportPath && isPathSafe(healthReportPath)) {
            try {
              const reportBuffer = await fs.readFile(healthReportPath);
              attachments.push({
                filename: "Flyerz.co.za Artwork Intellegence Proof and Report.pdf",
                content: reportBuffer,
              });
            } catch (e) {
              console.warn('[FAI][Share] Could not attach health report:', e);
            }
          }

          await resend.emails.send({
            from: 'Flyerz.co.za Artwork Intelligence <onboarding@resend.dev>',
            to: ['sales@flyerz.co.za'],
            subject: `New Lead: ${job.filename} — Print Ready Artwork Shared`,
            html: htmlBody,
            text: reportSummary,
            attachments,
          });

          emailSent = true;
          console.log(`[FAI][Share] Email sent to sales@flyerz.co.za for job #${jobId} (customer: ${email})`);
        } catch (emailErr: any) {
          console.error('[FAI][Share] Failed to send email:', emailErr.message || emailErr);
        }
      } else {
        console.log(`[FAI][Share] RESEND_API_KEY not set — email NOT sent. Details logged below:`);
        console.log(`[FAI][Share] Customer email: ${email}`);
        console.log(`[FAI][Share] Job: #${jobId} — ${job.filename}`);
        console.log(`[FAI][Share] Job URL: ${jobUrl}`);
        console.log(`[FAI][Share] Report Summary:\n${reportSummary}`);
      }

      res.json({
        success: true,
        emailSent,
        message: emailSent
          ? 'Your report and files have been shared with Flyerz.co.za!'
          : 'Share request recorded. Email will be sent once the email service is configured.',
      });
    } catch (error: any) {
      console.error('[FAI][Share] Error:', error);
      res.status(500).json({ message: 'Failed to share report' });
    }
  });

  const GLITCHY_REPORTS_FILE = path.join(process.cwd(), 'glitchy_reports.json');
  const GLITCHY_CURSOR_AGENT_SCRIPT = path.join(process.cwd(), "server", "glitchy_cursor_agent.py");

  function maybeTriggerGlitchyCursorAgent(report: Record<string, any>): void {
    const apiKey = (process.env.CURSOR_API_KEY || "").trim();
    const repoUrl = (process.env.GITHUB_REPO_URL || "").trim();
    if (!apiKey || !repoUrl) {
      return;
    }
    if (!fsSync.existsSync(GLITCHY_CURSOR_AGENT_SCRIPT)) {
      console.warn("[Glitchy] Cursor agent script missing — skipping cloud agent launch");
      return;
    }

    const payload = {
      user_feedback: report.message || report.user_feedback || "",
      crop_box: report.crop_box ?? null,
      gs_logs: report.gs_logs || "",
      page_state: report.page_state || {
        page: report.page,
        jobId: report.jobId ?? null,
        is_no_crop: report.is_no_crop === true,
        full_page_dimensions: report.full_page_dimensions ?? null,
      },
    };
    if (!payload.user_feedback) return;

    try {
      const child = spawn(
        PYTHON_BIN,
        [GLITCHY_CURSOR_AGENT_SCRIPT, "--cli", JSON.stringify(payload)],
        {
          cwd: process.cwd(),
          env: process.env,
          stdio: "ignore",
          detached: true,
          windowsHide: true,
        },
      );
      child.on("error", (err) => {
        console.error("[Glitchy] Cursor agent process error:", err.message);
      });
      child.unref();
    } catch (err: any) {
      console.error("[Glitchy] Failed to spawn Cursor agent:", err?.message || err);
    }
  }

  app.post('/api/glitchy-report', async (req, res) => {
    try {
      let reports: any[] = [];
      try {
        const data = await fs.readFile(GLITCHY_REPORTS_FILE, 'utf-8');
        reports = JSON.parse(data);
      } catch { }
      reports.push(req.body);
      await fs.writeFile(GLITCHY_REPORTS_FILE, JSON.stringify(reports, null, 2));
      res.json({ status: "success" });
      // Fire-and-forget: never block or fail the user-facing report save.
      maybeTriggerGlitchyCursorAgent(req.body || {});
    } catch (error: any) {
      console.error('[Glitchy] Error saving report:', error);
      res.status(500).json({ message: 'Failed to save report' });
    }
  });

  app.get('/api/glitchy-admin-data', async (req, res) => {
    try {
      const data = await fs.readFile(GLITCHY_REPORTS_FILE, 'utf-8');
      const reports = JSON.parse(data);
      res.json(reports.reverse());
    } catch {
      res.json([]);
    }
  });

  app.post('/api/glitchy-delete', async (req, res) => {
    try {
      const { timestamp } = req.body;
      const data = await fs.readFile(GLITCHY_REPORTS_FILE, 'utf-8');
      let reports = JSON.parse(data);
      const filtered = reports.filter((r: any) => r.timestamp !== timestamp);
      await fs.writeFile(GLITCHY_REPORTS_FILE, JSON.stringify(filtered, null, 2));
      res.json({ success: true });
    } catch {
      res.status(404).json({ message: "Reports file not found" });
    }
  });

  app.get('/api/glitchy-checklist/:jobId', async (req, res) => {
    try {
      const jobId = parseInt(req.params.jobId);
      if (isNaN(jobId)) return res.json({ checks: [] });
      const job = await storage.getJob(jobId);
      if (!job || !job.auditResults) return res.json({ checks: [] });

      const auditChecks = stripCropBoxNotInMediaBoxFromChecks(job.auditResults.checks || []);
      const overallPassed = job.auditResults.overallPassed === true;
      const checks: { label: string; pass: boolean }[] = [];

      const dpiCheck = auditChecks.find((c: any) => c.name?.toLowerCase().includes("dpi"));
      if (dpiCheck) checks.push({ label: "High Res (300 DPI)", pass: dpiCheck.passed });

      const bleedCheck = auditChecks.find((c: any) => c.name?.toLowerCase().includes("bleed"));
      if (bleedCheck) checks.push({ label: "Bleed Ready", pass: bleedCheck.passed });

      const cmykCheck = auditChecks.find((c: any) => c.name?.toLowerCase().includes("cmyk") || c.name?.toLowerCase().includes("color"));
      if (cmykCheck) checks.push({ label: "CMYK Colors", pass: cmykCheck.passed });

      const sizeCheck = auditChecks.find((c: any) => c.name?.toLowerCase().includes("size") || c.name?.toLowerCase().includes("dimension"));
      if (sizeCheck) checks.push({ label: "Correct Size", pass: sizeCheck.passed });

      if (overallPassed) {
        for (const row of checks) {
          row.pass = true;
        }
      }

      res.json({ checks });
    } catch {
      res.json({ checks: [] });
    }
  });

  app.post('/api/glitchy-chat', async (req, res) => {
    try {
      const { message, jobId } = req.body;
      const msg = (message || '').toLowerCase();
      let response = "";

      let artworkState: any = null;
      const warnings: string[] = [];
      if (jobId) {
        try {
          const job = await storage.getJob(jobId);
          if (job) {
            const checks = stripCropBoxNotInMediaBoxFromChecks(job.auditResults?.checks || []);
            artworkState = {
              filename: job.filename,
              status: job.status,
              checks,
              overallPassed: job.auditResults?.overallPassed,
              aiEnhanced: job.auditResults?.aiEnhanced,
              bleedMethod: job.auditResults?.selectedBleedMethod || job.auditResults?.recommendedBleedMethod,
              hasBleed: checks.some((c: any) => c.name?.toLowerCase().includes("bleed") && c.passed),
              currentSize: (() => {
                const sizeCheck = checks.find((c: any) => c.name?.toLowerCase().includes("size") || c.name?.toLowerCase().includes("dimension"));
                return sizeCheck?.message || "processed";
              })(),
            };
            const dpiCheck = checks.find((c: any) => c.name?.toLowerCase().includes("dpi"));
            if (dpiCheck && !dpiCheck.passed) warnings.push("low_res");
            const bleedCheck = checks.find((c: any) => c.name?.toLowerCase().includes("bleed"));
            if (bleedCheck && !bleedCheck.passed) warnings.push("no_bleed");
            const cmykCheck = checks.find((c: any) => c.name?.toLowerCase().includes("cmyk") || c.name?.toLowerCase().includes("color"));
            if (cmykCheck && !cmykCheck.passed) warnings.push("wrong_color");
          }
        } catch { }
      }

      if (warnings.length > 0 && (msg.includes("next") || msg.includes("step") || msg.includes("what now"))) {
        if (warnings.includes("low_res")) {
          response = "Wait! Your DPI is too low. It might look blurry when printed! Can we fix that before the next step? 🔍";
        } else if (warnings.includes("no_bleed")) {
          response = "Hold on! I don't see proper bleed. Your edges might get cut off during trimming! 📏";
        } else if (warnings.includes("wrong_color")) {
          response = "Careful! The colors aren't in CMYK yet. They might shift when printed! 🎨";
        }
      } else if (msg.includes("hello") || msg.includes("hi") || msg.includes("hey")) {
        response = "Hiya! I'm Glitchy, your tiny print-shop assistant! ✨";
      } else if (msg.includes("day") || msg.includes("how are")) {
        const silly = [
          "I'm feeling 100% fluffy today!",
          "Just eating some leftover pixels!",
          "Optimizing my cuteness... standby!",
        ];
        response = silly[Math.floor(Math.random() * silly.length)];
      } else if (msg.includes("bleed")) {
        if (artworkState) {
          if (artworkState.hasBleed) {
            const bleedCheck = artworkState.checks.find((c: any) => c.name?.toLowerCase().includes("bleed"));
            response = bleedCheck
              ? `Yup! ${bleedCheck.message}. Method: ${artworkState.bleedMethod || "auto"}. You're safe! ✨`
              : "Bleed is added. Your edges are safe! ✨";
          } else {
            response = "I don't see proper bleed yet. The system will add it during processing! 🤔";
          }
        } else {
          response = "Bleed adds extra space so nothing gets cut off during printing. Upload a file to get started! 📏";
        }
      } else if (msg.includes("dpi") || msg.includes("resolution") || msg.includes("resize") || msg.includes("size")) {
        if (artworkState) {
          const dpiCheck = artworkState.checks.find((c: any) => c.name?.toLowerCase().includes("dpi"));
          response = dpiCheck
            ? `I've crunched the numbers: ${dpiCheck.message}. ${artworkState.aiEnhanced ? "AI enhancement was applied! 🤖" : "Looking sharp!"}`
            : "DPI looks good on this file!";
        } else {
          response = "For print, you need at least 300 DPI. Upload your artwork and I'll check it for you! 🔍";
        }
      } else if (msg.includes("next") || msg.includes("what now") || msg.includes("step")) {
        if (artworkState) {
          if (artworkState.status === "complete" && artworkState.overallPassed) {
            response = "Everything looks green! Your next step is to download the corrected file or share the report. 🚀";
          } else if (artworkState.status === "complete") {
            response = "Some checks need attention. Review the failed items and re-upload a corrected version! 🔧";
          } else {
            response = "Your file is still processing. Hang tight! ⏳";
          }
        } else {
          response = "Upload your artwork first, then I'll guide you through each step! 🚀";
        }
      } else if (msg.includes("warning") || msg.includes("issue") || msg.includes("problem")) {
        if (warnings.length > 0) {
          const issues = warnings.map(w => w === "low_res" ? "low DPI" : w === "no_bleed" ? "missing bleed" : w === "wrong_color" ? "not CMYK" : w);
          response = `I spotted ${issues.length} issue${issues.length > 1 ? "s" : ""}: ${issues.join(", ")}. Want me to explain any of these? ⚠️`;
        } else if (artworkState) {
          response = "No issues detected! Everything looks good on this file. 🎉";
        } else {
          response = "Upload a file first and I'll check it for issues! 🔍";
        }
      } else if (msg.includes("help") || msg.includes("what can")) {
        response = "I can help with: bleed, DPI/resolution, colors, warnings, and next steps. Just ask! 🌟";
      } else if (msg.includes("cmyk") || msg.includes("color") || msg.includes("colour")) {
        if (artworkState) {
          const cmykCheck = artworkState.checks.find((c: any) => c.name?.toLowerCase().includes("cmyk") || c.name?.toLowerCase().includes("color"));
          response = cmykCheck ? `Color check says: ${cmykCheck.message} 🎨` : "Colors are looking good! 🎨";
        } else {
          response = "For litho printing, artwork needs to be in CMYK color mode. Upload your file and I'll convert it! 🎨";
        }
      } else {
        response = "I'm not sure, but I'm tiny and learning! Ask me about your bleed, DPI, colors, or next steps. 🤔";
      }

      res.json({ reply: response });
    } catch (error: any) {
      console.error('[Glitchy] Chat error:', error);
      res.json({ reply: "Oops, my brain glitched! Try again? 🤯" });
    }
  });

  app.post('/api/batch-upload', upload.array('files', 20), async (req, res) => {
    try {
      const files = req.files as Express.Multer.File[] | undefined;
      if (!files || files.length === 0) {
        return res.status(400).json({ message: 'No files uploaded' });
      }

      let bleedOptions: ReturnType<typeof sanitizeBleedOptions> | undefined;
      try {
        const mm = readTargetMmFromForm(req.body);
        let merged: Record<string, any> = {};
        if (req.body.bleedOptions) {
          const parsed = parseJsonField(req.body.bleedOptions);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            merged = { ...parsed, ...mm };
          } else {
            merged = { ...mm };
          }
        } else {
          merged = { ...mm };
        }
        bleedOptions = sanitizeBleedOptions(coerceSavedBleedOptionsFromDb(merged));
      } catch (e) {
        console.warn('[FAI] Invalid bleedOptions JSON in batch upload, using defaults', e);
        bleedOptions = sanitizeBleedOptions(coerceSavedBleedOptionsFromDb({}));
      }

      const jobIds: number[] = [];
      for (const file of files) {
        const fileType = path.extname(file.originalname).toLowerCase().replace('.', '') as any;
        const normalizedType = fileType === 'jpeg' ? 'jpg' : fileType;
        const job = await storage.createJob({
          filename: file.originalname,
          originalPath: file.path,
          fileSize: file.size,
          fileType: normalizedType,
        });
        await storage.updateJob(job.id, { status: 'processing' });
        jobIds.push(job.id);
        processFile(job.id, true, bleedOptions).catch((error: Error) => {
          console.error(`[FAI] Batch file processing failed for job ${job.id}:`, error.message);
          storage.updateJob(job.id, {
            status: 'failed',
            errorMessage: error.message,
            completedAt: new Date(),
          });
        });
      }

      return res.status(201).json({ jobIds });
    } catch (error: any) {
      console.error('[FAI] Batch upload error:', error);
      return res.status(500).json({ message: error.message || 'Batch upload failed' });
    }
  });

  app.get('/api/batch-download', async (req, res) => {
    try {
      const idsParam = req.query.ids as string;
      if (!idsParam) {
        return res.status(400).json({ message: 'Missing ids query parameter' });
      }
      const ids = idsParam.split(',').map(Number).filter(n => !isNaN(n));
      if (ids.length === 0) {
        return res.status(400).json({ message: 'No valid job IDs provided' });
      }

      const archiver = (await import('archiver')).default;
      const filePaths: { name: string; filePath: string }[] = [];

      for (const id of ids) {
        const job = await storage.getJob(id);
        if (!job) continue;
        if (job.status !== 'complete' || !job.correctedPath) continue;
        if (!isPathSafe(job.correctedPath)) continue;
        try {
          await fs.access(job.correctedPath);
          filePaths.push({ name: job.filename, filePath: job.correctedPath });
        } catch {
          // skip missing files
        }
      }

      if (filePaths.length === 0) {
        return res.status(404).json({ message: 'No completed files found for the given job IDs' });
      }

      res.setHeader('Content-Type', 'application/zip');
      res.setHeader('Content-Disposition', 'attachment; filename="print-ready-bundle.zip"');

      const archive = archiver('zip', { zlib: { level: 5 } });
      archive.on('error', (err: Error) => {
        console.error('[FAI] Archive error:', err);
        if (!res.headersSent) {
          res.status(500).json({ message: 'Failed to create zip archive' });
        }
      });
      archive.pipe(res);

      const usedNames = new Set<string>();
      for (const { name, filePath } of filePaths) {
        let safeName = path.basename(name).replace(/[^a-zA-Z0-9._-]/g, '_');
        if (usedNames.has(safeName)) {
          const ext = path.extname(safeName);
          const base = path.basename(safeName, ext);
          let counter = 1;
          while (usedNames.has(`${base}_${counter}${ext}`)) counter++;
          safeName = `${base}_${counter}${ext}`;
        }
        usedNames.add(safeName);
        archive.file(filePath, { name: `print-ready-${safeName}` });
      }

      await archive.finalize();
    } catch (error: any) {
      console.error('[FAI] Batch download error:', error);
      if (!res.headersSent) {
        return res.status(500).json({ message: error.message || 'Batch download failed' });
      }
    }
  });

  // Seed database with example jobs (for demo purposes)
  app.get('/api/test-pdf/download', async (_req, res) => {
    try {
      const filePath = path.resolve('stress_test.pdf');
      try {
        await fs.access(filePath);
      } catch {
        const { execSync: exec } = await import('child_process');
        exec(`${PYTHON_BIN} make_test.py`, { cwd: path.resolve('.'), timeout: 30000 });
      }
      await fs.access(filePath);
      res.setHeader('Content-Disposition', 'attachment; filename="stress_test.pdf"');
      res.setHeader('Content-Type', 'application/pdf');
      const stream = fsSync.createReadStream(filePath);
      stream.pipe(res);
    } catch (err: any) {
      res.status(500).json({ error: 'Failed to generate stress_test.pdf', details: err?.message });
    }
  });

  async function seedDatabase() {
    const existingJobs = await storage.getJobs();
    if (existingJobs.length === 0) {
      console.log('Seeding database with example jobs...');
    }
  }

  await seedDatabase();

  return httpServer;
}
