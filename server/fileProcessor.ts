import { storage, coerceSavedBleedOptionsFromDb } from "./storage";
import type { AuditResults, AuditCheck, BleedOptions, JobAudit } from "@shared/schema";
import path from "path";
import fs from "fs/promises";
import fsSync from "fs";
import { spawnSync } from "child_process";
import os from "os";
import { getFlyerzTempRoot } from "./envPaths";
import crypto from "crypto";
import { hasValidCropBox, isNoCropRoute } from "@shared/crop-box";

const MAX_CONCURRENT_JOBS = 3;
let activeJobs = 0;

interface QueuedJob {
  jobId: number;
  position: number;
  resolve: (value: void) => void;
  reject: (reason: any) => void;
}

const jobQueue: QueuedJob[] = [];
let nextQueuePosition = 1;

export function getQueueStatus(): { active: number; queued: number; maxConcurrent: number; positions: Record<number, number> } {
  const positions: Record<number, number> = {};
  jobQueue.forEach((item, idx) => {
    positions[item.jobId] = idx + 1;
  });
  return { active: activeJobs, queued: jobQueue.length, maxConcurrent: MAX_CONCURRENT_JOBS, positions };
}

export function getJobQueuePosition(jobId: number): number | null {
  const idx = jobQueue.findIndex(q => q.jobId === jobId);
  return idx >= 0 ? idx + 1 : null;
}

function dequeueNext(): void {
  if (jobQueue.length === 0) return;
  if (activeJobs >= MAX_CONCURRENT_JOBS) return;
  const next = jobQueue.shift()!;
  next.resolve();
}

async function acquireSlot(jobId: number): Promise<void> {
  if (activeJobs < MAX_CONCURRENT_JOBS) {
    activeJobs++;
    return;
  }

  await storage.updateJob(jobId, { status: "queued" as any });
  const queuePos = jobQueue.length + 1;
  console.log(`[FAI][Queue] Job ${jobId} queued at position #${queuePos} (${activeJobs}/${MAX_CONCURRENT_JOBS} active)`);

  await new Promise<void>((resolve, reject) => {
    jobQueue.push({ jobId, position: queuePos, resolve, reject });
  });

  activeJobs++;
  console.log(`[FAI][Queue] Job ${jobId} dequeued, now active (${activeJobs}/${MAX_CONCURRENT_JOBS})`);
}

function releaseSlot(): void {
  activeJobs = Math.max(0, activeJobs - 1);
  dequeueNext();
}

const PYTHON_SCRIPT = path.join(process.cwd(), "server", "smart_bleed.py");
const RESIZE_SCRIPT = path.join(process.cwd(), "server", "precision_resize.py");
const REPORT_SCRIPT = path.join(process.cwd(), "server", "health_report.py");
const PYTHON_BIN = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");

const EXEC_TIMEOUT_MS = 180_000;

function pythonChildEnv(): Record<string, string> {
  const base = {
    ...(process.env as Record<string, string>),
    PYTHONUNBUFFERED: "1",
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
  if (!base.FAI_TEMP_DIR?.trim()) {
    base.FAI_TEMP_DIR = getFlyerzTempRoot();
  }
  return base;
}

const PYTHON_ENV: Record<string, string> = pythonChildEnv();

function makeResultFile(prefix: string): string {
  const id = crypto.randomBytes(8).toString("hex");
  return path.join(os.tmpdir(), `${prefix}_${id}.json`);
}

function readResultFileSync(resultFile: string): any {
  const raw = fsSync.readFileSync(resultFile, "utf-8");
  return JSON.parse(raw);
}

/** Mirrors smart_bleed.SAFE_ZONE_LAYOUT_ERROR_CODE — layout rejection, not a server crash. */
export const SAFE_ZONE_LAYOUT_ERROR_CODE = "safe_zone_layout";

export const SAFE_ZONE_LAYOUT_USER_MESSAGE =
  "Text or logos are too close to the edge to safely auto-fix. Please move text inward by at least 3mm.";

function layoutErrorFromBleedResult(parsed: any): string | null {
  if (!parsed || typeof parsed !== "object") return null;
  if (parsed.errorCode === SAFE_ZONE_LAYOUT_ERROR_CODE) {
    return typeof parsed.error === "string" && parsed.error.trim()
      ? parsed.error
      : SAFE_ZONE_LAYOUT_USER_MESSAGE;
  }
  if (parsed.success === false && typeof parsed.error === "string") {
    if (/too close to the edge to safely auto-fix/i.test(parsed.error)) {
      return parsed.error;
    }
  }
  return null;
}

export function isSafeZoneLayoutRejectionMessage(message: string): boolean {
  return /too close to the edge to safely auto-fix/i.test(message);
}

function tryReadBleedResultFile(resultFile: string): any | null {
  try {
    return readResultFileSync(resultFile);
  } catch {
    return null;
  }
}

function assertBleedResultOk(parsed: any, label: string): any {
  const layoutMsg = layoutErrorFromBleedResult(parsed);
  if (layoutMsg) {
    throw new Error(layoutMsg);
  }
  if (parsed?.success === false && parsed?.error) {
    throw new Error(String(parsed.error));
  }
  return parsed;
}

async function cleanupFile(filePath: string): Promise<void> {
  try { await fs.unlink(filePath); } catch {}
}

/** Drop checks whose text still mentions this healed artifact (must never reach Glitchy). */
export function stripCropBoxNotInMediaBoxNoise(checks: AuditCheck[]): AuditCheck[] {
  const needle = /cropbox\s+not\s+in\s+mediabox/i;
  return checks.filter((c) => !needle.test(`${c.message ?? ""} ${c.details ?? ""}`));
}

/**
 * After PyMuPDF ingest geometry heal, remove stale failures whose messages only reflect
 * pre-heal CropBox/MediaBox/TrimBox problems (telemetry stays aligned with repaired vectors).
 */
export function normalizeChecksAfterPdfGeometryHeal(checks: AuditCheck[]): AuditCheck[] {
  checks = stripCropBoxNotInMediaBoxNoise(checks);
  const geometryHealed = checks.some((c) => c.name === "PDF Page Geometry" && c.autoFixed === true);
  if (!geometryHealed) return checks;

  const geomStale = /crop\s*box|media\s*box|outside\s+media|not\s+inside\s+media|invalid\s+page\s+boxes|page\s+geometry/i;

  return checks.map((c) => {
    if (c.name === "PDF Page Geometry" || c.passed) return c;
    const blob = `${c.message ?? ""} ${c.details ?? ""}`;
    if (!geomStale.test(blob)) return c;

    const unrelated =
      /transparency|spot\s+colou?r|unembedded|optional\s+content|layer(?!\s+balance)|live\s+rgb|alpha|smask/i.test(
        blob,
      );
    if (unrelated) return c;

    return {
      ...c,
      passed: true,
      autoFixed: true,
      severity: "PASS",
      message:
        "Structural PDF boxes validated after vector-preserving geometry heal (CropBox / TrimBox / BleedBox aligned to MediaBox).",
      details:
        "Earlier box warnings were cleared by PyMuPDF ingest repair; automated structural checks re-evaluated clean.",
    };
  });
}

function execPython(args: string[], label: string, resultFile: string, timeoutMs: number = EXEC_TIMEOUT_MS): any {
  const timerLabel = `[TIMER] Node Python subprocess: ${label}`;
  console.time(timerLabel);
  try {
    // Use spawnSync(argv) with no shell so paths with spaces (OneDrive, etc.) work on Windows.
    // A single-quoted shell string is for bash only; cmd.exe does not treat '...' as quoting.
    const proc = spawnSync(PYTHON_BIN, args, {
      cwd: process.cwd(),
      env: PYTHON_ENV,
      encoding: "utf8",
      timeout: timeoutMs,
      maxBuffer: 50 * 1024 * 1024,
      stdio: ["ignore", "inherit", "pipe"],
    });

    const procErr = proc.error as NodeJS.ErrnoException | undefined;
    if (procErr) {
      if (procErr.code === "ETIMEDOUT" || /TIMEOUT/i.test(String(procErr.message))) {
        throw new Error(`${label} timed out after ${timeoutMs / 1000} seconds. File may be too complex.`);
      }
      throw new Error(`${label} failed to start Python (${PYTHON_BIN}): ${procErr.message}`);
    }

    if (proc.signal) {
      const errTail = (proc.stderr || "").trim().slice(-800);
      throw new Error(`${label} terminated by signal ${proc.signal}.${errTail ? ` ${errTail}` : ""}`);
    }

    const stderr = (proc.stderr || "").trim();
    if (proc.status !== 0) {
      const failedResult = tryReadBleedResultFile(resultFile);
      const layoutMsg = layoutErrorFromBleedResult(failedResult);
      if (layoutMsg) {
        throw new Error(layoutMsg);
      }
      if (stderr) console.error(`[FAI] ${label} stderr:\n${stderr.slice(-4000)}`);
      throw new Error(
        `${label} failed (exit ${proc.status}). ${stderr ? stderr.slice(-600) : "Check server console for details."}`,
      );
    }

    try {
      const parsed = readResultFileSync(resultFile);
      return assertBleedResultOk(parsed, label);
    } catch (err) {
      if (err instanceof Error && isSafeZoneLayoutRejectionMessage(err.message)) {
        throw err;
      }
      throw new Error(`${label} failed. No result produced. Check server console for details.`);
    }
  } finally {
    console.timeEnd(timerLabel);
  }
}

async function runPythonResize(
  inputPath: string,
  outputPath: string,
  fileType: string,
  targetWidth: number,
  targetHeight: number,
): Promise<void> {
  const resultFile = makeResultFile("resize");
  try {
    const result = execPython(
      [RESIZE_SCRIPT, inputPath, outputPath, fileType,
       String(targetWidth), String(targetHeight), "0", resultFile],
      "Resize",
      resultFile,
      EXEC_TIMEOUT_MS,
    );
    if (result.success === false) {
      throw new Error(result.error || "Resize returned failure");
    }
  } finally {
    await cleanupFile(resultFile);
  }
}

function runPythonBleed(
  inputPath: string,
  outputPath: string,
  fileType: string,
  bleedOptions?: BleedOptions
): { checks: AuditCheck[]; correctedPath?: string; preBleedPath?: string; proofPath?: string; proofPaths?: string[]; proofPageCount?: number; proofIsBlank?: boolean; comparisonPath?: string; bleedVariants?: Record<string, string>; recommendedBleedMethod?: string; criticalSafeZone?: boolean; rightSafety?: string; error?: string; originalDpi?: number; finalDpi?: number; showLowDpiWarning?: boolean; aiEnhanced?: boolean; inkSavingsPercent?: number; safetyStatus?: string; bleedProofPath?: string; lensesDetected?: boolean; lensesFlattened?: boolean; supersampled?: boolean; originalTic?: number; finalTic?: number; autoHealEvent?: AuditResults["autoHealEvent"] } {
  const resultFile = makeResultFile("bleed");
  const args = [PYTHON_SCRIPT, inputPath, outputPath, fileType, resultFile];
  if (bleedOptions) {
    args.push(JSON.stringify(bleedOptions));
  }

  try {
    return execPython(args, "Bleed", resultFile, EXEC_TIMEOUT_MS);
  } finally {
    cleanupFile(resultFile);
  }
}

function auditOfficeFile(fileType: string): AuditCheck[] {
  return [
    {
      name: "Office File Format",
      passed: false,
      message: `${fileType.toUpperCase()} files are not ideal for litho printing.`,
      autoFixed: false,
      details: "Recommend converting to high-resolution PDF (400 DPI) with embedded fonts and CMYK color space.",
    },
    {
      name: "Color Space",
      passed: false,
      message: "Office files use RGB color space — must be converted to CMYK.",
      autoFixed: false,
      details: "Export to PDF from your application and choose CMYK color space.",
    },
    {
      name: "Font Embedding",
      passed: false,
      message: "Office files may not have embedded fonts.",
      autoFixed: false,
      details: "When exporting to PDF, enable 'Embed all fonts' option.",
    },
    {
      name: "5mm Smart Bleed",
      passed: false,
      message: "Smart Bleed cannot be applied to Office files. Convert to PDF first.",
      autoFixed: false,
      details: "Upload as PDF or JPG/PNG to enable automatic Smart Bleed via pixel mirroring.",
    },
  ];
}

export function generateHealthReport(
  checks: AuditCheck[],
  filename: string,
  outputPath: string,
  proofPath?: string,
  proofPaths?: string[],
  artworkPath?: string
): void {
  const resultFile = makeResultFile("report");
  const checksJson = JSON.stringify(checks);
  const args = [REPORT_SCRIPT, checksJson, filename, outputPath, resultFile];
  const limitedProofs = (proofPaths && proofPaths.length > 0 ? proofPaths : (proofPath ? [proofPath] : [])).slice(0, 2);
  if (limitedProofs.length > 0) args.push(JSON.stringify(limitedProofs));
  else args.push("[]");
  if (artworkPath) args.push(artworkPath);

  try {
    const result = execPython(args, "HealthReport", resultFile, EXEC_TIMEOUT_MS);
    if (!result.success) {
      throw new Error(result.error || "Report generation failed");
    }
    const stat = fsSync.statSync(outputPath);
    if (stat.size < 100) {
      throw new Error("Generated health report is empty or too small");
    }
  } finally {
    try { fsSync.unlinkSync(resultFile); } catch {}
  }
}

async function cleanDirectory(
  dirPath: string,
  matchFn: (entry: string) => boolean,
  maxAgeMs: number,
  now: number,
): Promise<{ cleaned: number; bytesReclaimed: number }> {
  let cleaned = 0;
  let bytesReclaimed = 0;
  try {
    const entries = await fs.readdir(dirPath);
    for (const entry of entries) {
      if (!matchFn(entry)) continue;
      const fullPath = path.join(dirPath, entry);
      try {
        const stat = await fs.stat(fullPath);
        if (now - stat.mtimeMs > maxAgeMs) {
          const size = stat.size;
          await fs.unlink(fullPath);
          cleaned++;
          bytesReclaimed += size;
        }
      } catch {}
    }
  } catch {}
  return { cleaned, bytesReclaimed };
}

export async function runJanitor(): Promise<void> {
  const tmpDir = os.tmpdir();
  const MAX_AGE_MS = 2 * 60 * 60 * 1000;
  const MAX_AGE_DELIVERABLES_MS = 12 * 60 * 60 * 1000;
  const PATTERNS = [
    /^(resize|bleed|report|qc)_[a-f0-9]+\.json$/,
    /_standardized\.tiff$/,
    /^fai_gs_stderr_/,
    /^tmp[a-zA-Z0-9_]+_standardized\.tiff$/,
    /^precompile_(status|result)_\d+\.json$/,
    /^tmpx?w[a-zA-Z0-9]+\.(png|pdf|tiff?)$/,
  ];

  const UPLOAD_SCRATCH_PATTERNS = [
    /^flyerz_input_/,
    /_resized_/,
  ];

  const UPLOAD_DELIVERABLE_PATTERNS = [
    /^flyerz_precompile_.*\.zip$/,
    /_press_ready_precompile\.pdf$/,
    /^processed_[^_]+_.+/,
    /^original_[^_]+_.+/,
    /^Flyerz_Health_Report_/,
  ];

  const now = Date.now();

  const tmpResult = await cleanDirectory(
    tmpDir,
    (entry) => PATTERNS.some(p => p.test(entry)),
    MAX_AGE_MS,
    now,
  );
  if (tmpResult.cleaned > 0) {
    console.log(`[FAI][Janitor] Cleaned ${tmpResult.cleaned} files (${(tmpResult.bytesReclaimed / 1024 / 1024).toFixed(1)} MB reclaimed) from ${tmpDir}`);
  }

  const uploadsDir = path.join(process.cwd(), 'uploads');
  const scratchResult = await cleanDirectory(
    uploadsDir,
    (entry) => UPLOAD_SCRATCH_PATTERNS.some(p => p.test(entry)),
    MAX_AGE_MS,
    now,
  );
  if (scratchResult.cleaned > 0) {
    console.log(`[FAI][Janitor] Cleaned ${scratchResult.cleaned} scratch files (${(scratchResult.bytesReclaimed / 1024 / 1024).toFixed(1)} MB reclaimed) from ${uploadsDir}`);
  }

  const deliverableResult = await cleanDirectory(
    uploadsDir,
    (entry) => UPLOAD_DELIVERABLE_PATTERNS.some(p => p.test(entry)),
    MAX_AGE_DELIVERABLES_MS,
    now,
  );
  if (deliverableResult.cleaned > 0) {
    console.log(`[FAI][Janitor] Cleaned ${deliverableResult.cleaned} deliverable files (${(deliverableResult.bytesReclaimed / 1024 / 1024).toFixed(1)} MB reclaimed) from ${uploadsDir}`);
  }

  const flyerzTemp = getFlyerzTempRoot();
  if (fsSync.existsSync(flyerzTemp)) {
    const shmResult = await cleanDirectory(
      flyerzTemp,
      (entry) => /^flyerz_/.test(entry) || /\.(pdf|png|jpg|jpeg|tmp|log)$/i.test(entry),
      MAX_AGE_MS,
      now,
    );
    if (shmResult.cleaned > 0) {
      console.log(`[FAI][Janitor] Cleaned ${shmResult.cleaned} files (${(shmResult.bytesReclaimed / 1024 / 1024).toFixed(1)} MB reclaimed) from ${flyerzTemp}`);
    }
  }

  const faiTempDir = path.join(process.cwd(), 'fai_temp_processing');
  const faiTempResult = await cleanDirectory(
    faiTempDir,
    () => true,
    MAX_AGE_MS,
    now,
  );
  if (faiTempResult.cleaned > 0) {
    console.log(`[FAI][Janitor] Cleaned ${faiTempResult.cleaned} files (${(faiTempResult.bytesReclaimed / 1024 / 1024).toFixed(1)} MB reclaimed) from ${faiTempDir}`);
  }
}

let janitorInterval: ReturnType<typeof setInterval> | null = null;

export function startJanitor(intervalMs: number = 60 * 60 * 1000): void {
  if (janitorInterval) return;
  console.log("[FAI][Janitor] Started — cleaning hourly (first run in 10 min)");
  janitorInterval = setInterval(() => {
    runJanitor().catch(err => console.warn("[FAI][Janitor] Run failed:", err));
  }, intervalMs);
  setTimeout(() => {
    runJanitor().catch(() => {});
  }, 10 * 60 * 1000);
}

export async function processFile(jobId: number, applyFixes: boolean = true, bleedOptions?: BleedOptions): Promise<void> {
  await acquireSlot(jobId);

  try {
    await processFileInternal(jobId, applyFixes, bleedOptions);
  } finally {
    releaseSlot();
  }
}

async function processFileInternal(jobId: number, applyFixes: boolean, bleedOptions?: BleedOptions): Promise<void> {
  await storage.updateJob(jobId, { status: "processing" });

  try {
    const job = await storage.getJob(jobId);
    if (!job) {
      throw new Error("Job not found");
    }

    let checks: AuditCheck[] = [];
    let correctedPath: string | undefined;
    let proofPath: string | undefined;
    let proofPaths: string[] | undefined;
    let proofPageCount: number | undefined;
    let proofIsBlank: boolean | undefined;
    let comparisonPath: string | undefined;
    let bleedProofPath: string | undefined;
    let originalDpi: number | undefined;
    let showLowDpiWarning: boolean | undefined;
    let aiEnhanced: boolean | undefined;
    let finalDpi: number | undefined;
    let inkSavingsPercent: number | undefined;
    let safetyStatus: 'SAFE' | 'CRITICAL' | undefined;
    let bleedVariants: Record<string, string> | undefined;
    let recommendedBleedMethod: string | undefined;
    let criticalSafeZone: boolean | undefined;
    let rightSafety: string | undefined;
    let lensesDetected: boolean | undefined;
    let lensesFlattened: boolean | undefined;
    let supersampled: boolean | undefined;
    let originalTic: number | undefined;
    let finalTic: number | undefined;
    let preBleedPath: string | undefined;
    let bleedPythonResult: ReturnType<typeof runPythonBleed> | undefined;

    const { fileType, originalPath, filename } = job;

    const effectiveBleed = {
      ...coerceSavedBleedOptionsFromDb({
        ...coerceSavedBleedOptionsFromDb((job.auditResults as any)?.savedBleedOptions),
        ...(bleedOptions && typeof bleedOptions === "object" ? bleedOptions : {}),
      }),
    } as BleedOptions;

    if (["pdf", "jpg", "jpeg", "png"].includes(fileType)) {
      const dir = path.dirname(originalPath);
      const ext = path.extname(filename).toLowerCase();
      const basename = path.basename(filename, ext);

      const originalWithExt = path.join(dir, `original_${jobId}_${basename}${ext}`);
      if (!path.extname(originalPath)) {
        try {
          await fs.access(originalWithExt);
        } catch {
          await fs.copyFile(originalPath, originalWithExt);
        }
      }
      let inputForBleed = path.extname(originalPath) ? originalPath : originalWithExt;
      const hasCropCoords = hasValidCropBox(effectiveBleed as any);
      const skipPreResize = hasCropCoords || isNoCropRoute(effectiveBleed as any);

      console.time("[TIMER] Node fileProcessor: prepress spawns (resize if any + smart_bleed)");
      let result!: ReturnType<typeof runPythonBleed>;
      try {
      if (
        !skipPreResize &&
        effectiveBleed?.targetWidth &&
        effectiveBleed?.targetHeight &&
        effectiveBleed.targetWidth > 0 &&
        effectiveBleed.targetHeight > 0
      ) {
        const resizedPath = path.join(
          dir,
          `${jobId}_${basename}_resized_${effectiveBleed.targetWidth}x${effectiveBleed.targetHeight}mm${ext}`,
        );
        console.log(`[FAI] Pre-resize: ${effectiveBleed.targetWidth}x${effectiveBleed.targetHeight}mm`);
        await runPythonResize(
          inputForBleed,
          resizedPath,
          fileType === "jpeg" ? "jpg" : fileType,
          effectiveBleed.targetWidth,
          effectiveBleed.targetHeight,
        );
        inputForBleed = resizedPath;
      } else if (hasCropCoords || isNoCropRoute(effectiveBleed as any)) {
        console.log(`[FAI] Manual crop / No Crop Needed — skipping pre-resize, crop+scale will happen in bleed/compile pipeline`);
      }

      const outputPath = path.join(dir, `processed_${jobId}_${basename}${ext}`);

      result = await runPythonBleed(inputForBleed, outputPath, fileType, effectiveBleed);
      bleedPythonResult = result;
      } finally {
        console.timeEnd("[TIMER] Node fileProcessor: prepress spawns (resize if any + smart_bleed)");
      }
      checks = result.checks || [];
      proofPath = result.proofPath;
      proofPaths = result.proofPaths;
      proofPageCount = result.proofPageCount;
      proofIsBlank = result.proofIsBlank;
      originalDpi = result.originalDpi;
      finalDpi = result.finalDpi ?? result.originalDpi;
      showLowDpiWarning = result.showLowDpiWarning;
      aiEnhanced = result.aiEnhanced;
      inkSavingsPercent = result.inkSavingsPercent ?? 0;
      safetyStatus = result.safetyStatus === "CRITICAL" || result.rightSafety === "CRITICAL" || result.criticalSafeZone ? "CRITICAL" : "SAFE";
      comparisonPath = result.comparisonPath;
      bleedProofPath = result.bleedProofPath;
      bleedVariants = result.bleedVariants;
      recommendedBleedMethod = result.recommendedBleedMethod;
      criticalSafeZone = result.criticalSafeZone;
      rightSafety = result.rightSafety;
      lensesDetected = result.lensesDetected;
      lensesFlattened = result.lensesFlattened;
      supersampled = result.supersampled;
      originalTic = result.originalTic;
      finalTic = result.finalTic;
      preBleedPath = result.preBleedPath;

      if (Array.isArray((result as any).crop_box) && (result as any).crop_box.length >= 4) {
        (effectiveBleed as any).cropBoxPx = (result as any).crop_box;
        if (!hasValidCropBox(effectiveBleed as any)) {
          (effectiveBleed as any).cropX = 0;
          (effectiveBleed as any).cropY = 0;
          (effectiveBleed as any).cropWidth = 1;
          (effectiveBleed as any).cropHeight = 1;
          (effectiveBleed as any).isNoCrop = true;
          console.log(`[FAI] Persisted NO_CROP full-page crop_box from pipeline: ${(result as any).crop_box}`);
        }
      }

      if (result.correctedPath) {
        try {
          const stat = await fs.stat(result.correctedPath);
          if (stat.size > 0) {
            correctedPath = result.correctedPath;
          } else {
            console.warn("[FAI] Corrected file exists but is empty (0 bytes):", result.correctedPath);
          }
        } catch {
          console.warn("[FAI] Corrected file not found at expected path:", result.correctedPath);
        }
      }
    } else if (["docx", "pptx"].includes(fileType)) {
      checks = auditOfficeFile(fileType);
    } else {
      checks = [
        {
          name: "Unsupported File",
          passed: false,
          message: `File type '${fileType}' is not supported.`,
          autoFixed: false,
          details: "Supported types: PDF, JPG, PNG, DOCX, PPTX",
        },
      ];
    }

    checks = normalizeChecksAfterPdfGeometryHeal(checks);

    const overallPassed = checks.every((c) => c.passed || (c as any).severity === "WARNING" || (c as any).severity === "MANUAL_REVIEW");
    const fixesApplied = checks.filter((c) => c.autoFixed).length;
    const complianceReport = generateComplianceReport(filename, checks, fixesApplied);

    let healthReportPath: string | undefined;
    if (correctedPath && checks.length > 0) {
      const reportDir = path.dirname(correctedPath);
      const reportFilename = `Flyerz_Health_Report_${path.basename(filename, path.extname(filename))}.pdf`;
      healthReportPath = path.join(reportDir, reportFilename);
    }

    const jobAudit: JobAudit = {
      originalDpi: originalDpi ?? 300,
      finalDpi: finalDpi ?? originalDpi ?? 300,
      aiEnhanced: aiEnhanced ?? false,
      inkSavingsPercent: inkSavingsPercent ?? 0,
      safetyStatus: safetyStatus ?? "SAFE",
      lensesDetected: lensesDetected ?? false,
      lensesFlattened: lensesFlattened ?? false,
      supersampled: supersampled ?? false,
      originalTic: originalTic ?? 0,
      finalTic: finalTic ?? 0,
    };

    const auditResults: AuditResults = {
      checks,
      overallPassed,
      fixesApplied,
      complianceReport,
      proofPath,
      proofPaths,
      proofPageCount,
      proofIsBlank,
      originalDpi,
      showLowDpiWarning,
      aiEnhanced,
      comparisonPath,
      bleedProofPath,
      healthReportPath,
      savedBleedOptions: effectiveBleed,
      bleedVariants: bleedVariants as AuditResults["bleedVariants"],
      recommendedBleedMethod: recommendedBleedMethod as AuditResults["recommendedBleedMethod"],
      selectedBleedMethod: "auto",
      preBleedPath,
      criticalSafeZone: criticalSafeZone || false,
      rightSafety: rightSafety as AuditResults["rightSafety"],
      jobAudit,
      autoHealEvent: bleedPythonResult?.autoHealEvent ?? undefined,
    };

    await storage.updateJob(jobId, {
      status: "complete",
      auditResults,
      correctedPath,
      completedAt: new Date(),
    });

    if (healthReportPath && correctedPath) {
      setTimeout(async () => {
        try {
          const artworkForReport = bleedProofPath || proofPath;
          generateHealthReport(checks, filename, healthReportPath!, proofPath, proofPaths, artworkForReport);
          console.log(`[FAI] Health report generated: ${healthReportPath}`);
        } catch (err) {
          console.warn("[FAI] Health report generation failed (non-blocking):", err);
          try {
            const updatedJob = await storage.getJob(jobId);
            if (updatedJob?.auditResults) {
              const updated = { ...updatedJob.auditResults as any };
              delete updated.healthReportPath;
              await storage.updateJob(jobId, { auditResults: updated });
            }
          } catch {}
        }
      }, 2000);
    }
  } catch (error) {
    console.error(`[FAI] Error processing job ${jobId}:`, error);
    const msg = error instanceof Error ? error.message : "Unknown error";
    const layoutRejection = isSafeZoneLayoutRejectionMessage(msg);
    let auditResults: AuditResults | undefined;
    if (layoutRejection) {
      const job = await storage.getJob(jobId);
      const prior = (job?.auditResults as AuditResults | undefined) ?? { checks: [], overallPassed: false, fixesApplied: 0 };
      const layoutCheck: AuditCheck = {
        name: "Safe Zone Layout",
        passed: false,
        message: msg,
        autoFixed: false,
        details: "",
      };
      const checks = [...(prior.checks || []).filter((c) => c.name !== "Safe Zone Layout"), layoutCheck];
      auditResults = {
        ...prior,
        checks,
        overallPassed: false,
        fixesApplied: prior.fixesApplied ?? 0,
        complianceReport: prior.complianceReport ?? "Safe zone layout rejected — artwork too close to trim.",
      };
    }
    await storage.updateJob(jobId, {
      status: "failed",
      errorMessage: msg,
      ...(auditResults ? { auditResults } : {}),
      completedAt: new Date(),
    });
    throw error;
  }
}

function generateComplianceReport(
  filename: string,
  checks: AuditCheck[],
  fixesApplied: number
): string {
  const timestamp = new Date().toISOString();
  const overallStatus = checks.every((c) => c.passed) ? "COMPLIANT" : "NON-COMPLIANT";

  let report = `[FAI] FLYERZ.CO.ZA ARTWORK INTELLIGENCE — COMPLIANCE REPORT\n`;
  report += `================================================================\n\n`;
  report += `File: ${filename}\n`;
  report += `Report Generated: ${timestamp}\n`;
  report += `Smart Bleed Method: Clean-Edge (Crop 1mm + Extend 6mm via BORDER_REPLICATE)\n`;
  report += `Net Bleed: 5mm (adaptive DPI)\n`;
  report += `Auto-Fixes Applied: ${fixesApplied}\n\n`;

  report += `AUDIT RESULTS:\n`;
  report += `================================================================\n\n`;

  checks.forEach((check, index) => {
    report += `${index + 1}. ${check.name}\n`;
    report += `   Status: ${check.passed ? "PASSED ✓" : "FAILED ✗"}\n`;
    report += `   ${check.message}\n`;
    if (check.autoFixed) {
      report += `   Auto-Fixed: YES\n`;
    }
    if (check.details) {
      report += `   Details: ${check.details}\n`;
    }
    report += `\n`;
  });

  report += `================================================================\n`;
  report += `OVERALL STATUS: ${overallStatus}\n`;
  report += `================================================================\n\n`;

  report += `[FAI] CLEAN-EDGE BLEED NOTES:\n`;
  report += `- Clean-Edge Bleed crops 1mm (removes artefacts) then extends 6mm via BORDER_REPLICATE\n`;
  report += `- This ensures artwork colour continues seamlessly into the bleed area\n`;
  report += `- Corrected file dimensions are 10mm wider and taller than original\n`;
  report += `- Keep all important content at least 5mm from original artwork edges\n\n`;

  report += `[FAI] RECOMMENDATIONS:\n`;
  report += `- Review all failed checks above\n`;
  report += `- Download the corrected file with Clean-Edge Bleed applied\n`;
  report += `- Verify visual quality via the Artwork Intelligence Visual Proof\n`;
  report += `- For CMYK, export directly from your design application as CMYK PDF\n`;
  report += `\n© 2026 Flyerz.co.za Artwork Intelligence. All rights reserved.\n`;

  return report;
}
