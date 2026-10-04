import { spawn } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import type { AuditResults, FileType } from "@shared/schema";
import { quickPrintProduct, QUICK_PRINT_PRODUCTS } from "@shared/quickPrint";
import { pythonChildEnv } from "./pythonChildEnv";
import { storage } from "./storage";
import { jobProgressPath, writeJobProgress } from "./jobProgress";

const PYTHON_BIN = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");
const PRESS_LOG_TAG = /^\[[A-Za-z][A-Za-z0-9_-]*\]/;

/** Python lines such as [AI-UPSCALE] and [vector-trace], without the text-gate body. */
export function pressLogLines(stderr: string): string[] {
  const lines: string[] = [];
  for (const row of String(stderr || "").split(/\r?\n/)) {
    const line = row.trim();
    if (PRESS_LOG_TAG.test(line)) lines.push(line);
  }
  return lines;
}
const SCRIPT = path.join(process.cwd(), "server", "quick_print.py");

export interface QuickRunOptions {
  productId: string;
  trimW?: number;
  trimH?: number;
  quantity?: number | null;
  notes?: string;
  filename?: string;
  detectSize?: boolean;
}

export interface QuickRunResult {
  light: "green" | "amber" | "red";
  reasons: string[];
  decisions: string[];
  checklist: { id: string; label: string; passed: boolean; detail: string }[];
  clientMessage: string;
  pressPath: string;
  proofPng: string;
  proofPdf: string;
  bleedMm: number;
  existingBleedKept: boolean;
  productId: string;
  productLabel: string;
  quantity: number | null;
  notes: string;
  upscale: number;
  pressEngine: Record<string, unknown> | null;
  enginePassed: boolean;
  mediaWidthMm?: number;
  mediaHeightMm?: number;
  trimWidthMm?: number;
  trimHeightMm?: number;
  rebuildPdf?: string;
  rebuildBefore?: string;
  rebuildAfter?: string;
}

function asResult(raw: Record<string, unknown>): QuickRunResult {
  const light = raw.light === "green" || raw.light === "amber" || raw.light === "red" ? raw.light : "red";
  const list = (value: unknown) => (Array.isArray(value) ? value.map((item) => String(item)) : []);
  return {
    light,
    reasons: list(raw.reasons),
    decisions: list(raw.decisions),
    checklist: Array.isArray(raw.checklist)
      ? raw.checklist.map((item) => {
          const row = item && typeof item === "object" ? item as Record<string, unknown> : {};
          return {
            id: String(row.id || ""),
            label: String(row.label || ""),
            passed: row.passed === true,
            detail: String(row.detail || ""),
          };
        })
      : [],
    clientMessage: String(raw.clientMessage || ""),
    pressPath: String(raw.pressPath || ""),
    proofPng: String(raw.proofPng || ""),
    proofPdf: String(raw.proofPdf || ""),
    bleedMm: Number(raw.bleedMm) || 5,
    existingBleedKept: raw.existingBleedKept === true,
    productId: String(raw.productId || ""),
    productLabel: String(raw.productLabel || ""),
    quantity: raw.quantity == null || raw.quantity === "" ? null : Number(raw.quantity),
    notes: String(raw.notes || ""),
    upscale: Number(raw.upscale) || 0,
    pressEngine: raw.pressEngine && typeof raw.pressEngine === "object" ? raw.pressEngine as Record<string, unknown> : null,
    enginePassed: raw.enginePassed === true,
    mediaWidthMm: raw.mediaWidthMm == null ? undefined : Number(raw.mediaWidthMm),
    mediaHeightMm: raw.mediaHeightMm == null ? undefined : Number(raw.mediaHeightMm),
    trimWidthMm: raw.trimWidthMm == null ? undefined : Number(raw.trimWidthMm),
    trimHeightMm: raw.trimHeightMm == null ? undefined : Number(raw.trimHeightMm),
    rebuildPdf: raw.rebuildPdf ? String(raw.rebuildPdf) : "",
    rebuildBefore: raw.rebuildBefore ? String(raw.rebuildBefore) : "",
    rebuildAfter: raw.rebuildAfter ? String(raw.rebuildAfter) : "",
  };
}

export function resolveQuickProduct(productId: string, customW?: number, customH?: number) {
  if (productId === "custom") {
    const width = Number(customW);
    const height = Number(customH);
    if (!Number.isFinite(width) || !Number.isFinite(height) || width < 20 || height < 20 || width > 900 || height > 900) {
      throw new Error("Custom size needs a width and height between 20 and 900 mm.");
    }
    return { id: "custom", label: `Custom ${width} × ${height} mm`, widthMm: width, heightMm: height };
  }
  if (productId === "auto") {
    const fallback = quickPrintProduct("a5") || QUICK_PRINT_PRODUCTS[0];
    return fallback;
  }
  const found = quickPrintProduct(productId);
  if (!found) throw new Error("Choose a product size.");
  return found;
}

export function fileTypeForName(filename: string): FileType {
  const ext = path.extname(filename || "").toLowerCase().replace(".", "");
  if (ext === "jpeg" || ext === "jpg") return "jpg";
  if (ext === "doc") return "docx";
  if (ext === "ppt") return "pptx";
  if (ext === "png" || ext === "pdf" || ext === "docx" || ext === "pptx" || ext === "ai" || ext === "eps") return ext;
  return "pdf";
}

export function runQuickPrintFile(inputPath: string, outputDir: string, options: QuickRunOptions & { jobId?: number }): Promise<QuickRunResult> {
  const product = options.productId === "custom"
    ? resolveQuickProduct("custom", options.trimW, options.trimH)
    : options.productId === "auto"
      ? resolveQuickProduct(options.productId)
      : resolveQuickProduct(options.productId);
  fs.mkdirSync(outputDir, { recursive: true });
  const args = [
    SCRIPT,
    "--input", inputPath,
    "--output-dir", outputDir,
    "--trim-w", String(product.widthMm),
    "--trim-h", String(product.heightMm),
    "--product-id", options.productId === "auto" ? "auto" : product.id,
    "--product-label", product.label,
    "--filename", options.filename || path.basename(inputPath),
    "--quantity", String(options.quantity || 0),
    "--notes", options.notes || "",
  ];
  if (options.detectSize || options.productId === "auto") args.push("--detect-size");
  const progressFile = options.jobId ? jobProgressPath(options.jobId) : "";
  if (progressFile) {
    if (options.jobId) writeJobProgress(options.jobId, "fitting");
    args.push("--progress-file", progressFile);
  }

  return new Promise((resolve, reject) => {
    const env = pythonChildEnv();
    if (progressFile) env.JOB_PROGRESS_FILE = progressFile;
    const child = spawn(PYTHON_BIN, args, {
      cwd: process.cwd(),
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
    }, 300_000);
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", () => {
      clearTimeout(timer);
      const line = stdout.split(/\r?\n/).map((row) => row.trim()).filter((row) => row.startsWith("{")).pop();
      const filePayload = path.join(outputDir, "result.json");
      let parsed: Record<string, unknown> | null = null;
      if (line) {
        try { parsed = JSON.parse(line); } catch { parsed = null; }
      }
      if (!parsed && fs.existsSync(filePayload)) {
        try { parsed = JSON.parse(fs.readFileSync(filePayload, "utf8")); } catch { parsed = null; }
      }
      if (!parsed) {
        resolve(asResult({
          light: "red",
          reasons: ["The file could not be read."],
          decisions: ["Quick mode could not finish."],
          clientMessage: "We could not open this file. Please send it again as a PDF, JPG, or PNG.",
          pressEngine: null,
        }));
        if (stderr.trim()) console.error(`[QUICK-PRINT] ${stderr.trim().slice(0, 500)}`);
        return;
      }
      for (const line of pressLogLines(stderr)) {
        console.error(`[QUICK-PRINT] ${line.slice(0, 500)}`);
      }
      resolve(asResult(parsed));
    });
  });
}

export async function saveQuickResult(jobId: number, result: QuickRunResult): Promise<void> {
  const enginePassed = result.pressEngine?.passed === true;
  const checks = result.decisions.map((message) => ({
    name: "Quick print",
    passed: result.light !== "red",
    message,
    autoFixed: true,
  }));
  for (const reason of result.reasons) {
    checks.push({ name: "Quick print", passed: false, message: reason, autoFixed: false });
  }
  if (result.light === "red" && result.clientMessage) {
    checks.push({ name: "Quick print", passed: false, message: result.clientMessage, autoFixed: false });
  }
  const { pressEngine, ...quickPrint } = result;
  const reused = publishQuickReuse(jobId, result);
  const audit = {
    checks,
    overallPassed: result.light === "green" && enginePassed,
    fixesApplied: result.light === "red" ? 0 : checks.length,
    complianceReport: result.decisions.join(" "),
    compiledPdfPath: result.pressPath || undefined,
    quickPrint: { ...quickPrint, approved: false },
    pressEngine,
    ...reused,
  } as AuditResults;
  await storage.updateJob(jobId, {
    status: "complete",
    correctedPath: result.pressPath || undefined,
    completedAt: new Date(),
    auditResults: audit,
  });
}

function copyIfPresent(source: string | undefined, dest: string): boolean {
  if (!source || !fs.existsSync(source)) return false;
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(source, dest);
  return true;
}

/** Point the job page at the picture quick mode already enlarged, so it does not call Replicate again. */
export function publishQuickReuse(jobId: number, result: QuickRunResult): Record<string, unknown> {
  const upload = path.join(process.cwd(), "uploads");
  const dir = jobOutputDir(jobId);
  const upscaled = fs.existsSync(path.join(dir, "upscaled.png")) ? path.join(dir, "upscaled.png") : result.rebuildAfter;
  const beforeSrc = fs.existsSync(path.join(dir, "fitted.png")) ? path.join(dir, "fitted.png") : result.rebuildBefore;
  const upBefore = path.join(upload, `ai-upscale-${jobId}-before.png`);
  const upAfter = path.join(upload, `ai-upscale-${jobId}-after.png`);
  const upFull = path.join(upload, `ai-upscale-${jobId}-full.png`);
  const copiedAfter = copyIfPresent(upscaled, upAfter);
  copyIfPresent(upscaled, upFull);
  copyIfPresent(beforeSrc, upBefore);
  if (!copiedAfter) return {};
  return {
    aiUpscale: {
      accepted: true,
      provider: "quick-print",
      enhancedPath: upFull,
      message: "Quick mode already enlarged this artwork. The original lettering was kept.",
      note: "Reused the quick-mode upscale.",
    },
  };
}

export function jobOutputDir(jobId: number): string {
  return path.join(process.cwd(), "uploads", "quick-print", String(jobId));
}

export function redFallback(message: string): QuickRunResult {
  return asResult({
    light: "red",
    reasons: ["The file could not be finished."],
    decisions: ["Sales quick mode decided this file on its own. Nobody was asked a question.", "Bleed is 5 mm on every side."],
    clientMessage: message,
    pressEngine: null,
  });
}

export function tempWorkDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-quick-"));
}
