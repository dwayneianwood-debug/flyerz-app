import { spawn } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import type { AuditCheck, AuditResults, FileType } from "@shared/schema";
import { quickPrintProduct, QUICK_PRINT_PRODUCTS } from "@shared/quickPrint";
import { decodeChildText, pythonChildEnv } from "./pythonChildEnv";
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
function quickPrintScript(): string {
  return process.env.QUICK_PRINT_SCRIPT || path.join(process.cwd(), "server", "quick_print.py");
}

function quickPrintKillMs(): number {
  const parsed = Number(process.env.QUICK_PRINT_KILL_MS || 300_000);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 300_000;
}

export interface QuickRunOptions {
  productId: string;
  trimW?: number;
  trimH?: number;
  quantity?: number | null;
  notes?: string;
  filename?: string;
  detectSize?: boolean;
  editRequest?: string;
  replicateEdit?: boolean;
  skipUpscale?: boolean;
  concerns?: string[];
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
  proofPaths: string[];
  pageCount: number;
  bleedMm: number;
  existingBleedKept: boolean;
  productId: string;
  productLabel: string;
  quantity: number | null;
  notes: string;
  upscale: number;
  pressEngine: Record<string, unknown> | null;
  enginePassed: boolean;
  bleedNote?: string;
  customerMessage?: string;
  beforePng?: string;
  mediaWidthMm?: number;
  mediaHeightMm?: number;
  trimWidthMm?: number;
  trimHeightMm?: number;
  rebuildPdf?: string;
  rebuildBefore?: string;
  rebuildAfter?: string;
  extraChecks?: { id?: string; num?: string; name?: string; label?: string; status?: string; pass?: boolean; detail?: string; message?: string; autoFixed?: boolean }[];
  prepressChecks?: { num?: string; name?: string; label?: string; status?: string; pass?: boolean; detail?: string }[];
}

export function tooLongMessage(bytes: number): string {
  const mb = Math.max(1, Math.round(Number(bytes) / (1024 * 1024)));
  return `This file took too long to process (${mb} MB); we're checking it manually`;
}

export function deadChildResult(timedOut: boolean, bytes: number): QuickRunResult {
  if (timedOut) {
    return asResult({
      light: "red",
      reasons: ["The file took too long."],
      decisions: ["Quick mode stopped because the file was still running."],
      clientMessage: tooLongMessage(bytes),
      pressEngine: null,
    });
  }
  return asResult({
    light: "red",
    reasons: ["The file could not be read."],
    decisions: ["Quick mode could not finish."],
    clientMessage: "We could not open this file. Please send it again as a PDF, JPG, or PNG.",
    pressEngine: null,
  });
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
    proofPaths: Array.isArray(raw.proofPaths) ? raw.proofPaths.map((item) => String(item)).filter(Boolean) : [],
    pageCount: Number(raw.pageCount) || 0,
    bleedMm: Number(raw.bleedMm) || 5,
    existingBleedKept: raw.existingBleedKept === true,
    productId: String(raw.productId || ""),
    productLabel: String(raw.productLabel || ""),
    quantity: raw.quantity == null || raw.quantity === "" ? null : Number(raw.quantity),
    notes: String(raw.notes || ""),
    upscale: Number(raw.upscale) || 0,
    pressEngine: raw.pressEngine && typeof raw.pressEngine === "object" ? raw.pressEngine as Record<string, unknown> : null,
    enginePassed: raw.enginePassed === true,
    bleedNote: String(raw.bleedNote || (raw.pressEngine && typeof raw.pressEngine === "object" ? (raw.pressEngine as Record<string, unknown>).bleedNote || "" : "")),
    customerMessage: String(raw.customerMessage || ""),
    beforePng: raw.beforePng ? String(raw.beforePng) : "",
    mediaWidthMm: raw.mediaWidthMm == null ? undefined : Number(raw.mediaWidthMm),
    mediaHeightMm: raw.mediaHeightMm == null ? undefined : Number(raw.mediaHeightMm),
    trimWidthMm: raw.trimWidthMm == null ? undefined : Number(raw.trimWidthMm),
    trimHeightMm: raw.trimHeightMm == null ? undefined : Number(raw.trimHeightMm),
    rebuildPdf: raw.rebuildPdf ? String(raw.rebuildPdf) : "",
    rebuildBefore: raw.rebuildBefore ? String(raw.rebuildBefore) : "",
    rebuildAfter: raw.rebuildAfter ? String(raw.rebuildAfter) : "",
    extraChecks: Array.isArray(raw.extraChecks)
      ? raw.extraChecks.map((item) => {
          const row = item && typeof item === "object" ? item as Record<string, unknown> : {};
          return {
            id: String(row.id || ""),
            name: String(row.name || ""),
            status: String(row.status || ""),
            pass: row.pass === true,
            detail: String(row.detail || row.message || ""),
            message: String(row.message || row.detail || ""),
            autoFixed: row.autoFixed === true || row.status === "fixed",
            num: String(row.num || ""),
            label: String(row.label || ""),
          };
        })
      : [],
    prepressChecks: Array.isArray(raw.prepressChecks)
      ? raw.prepressChecks.map((item) => {
          const row = item && typeof item === "object" ? item as Record<string, unknown> : {};
          const status = String(row.status || "");
          return {
            num: String(row.num || ""),
            name: String(row.name || ""),
            label: String(row.label || ""),
            status,
            pass: row.pass === true || status === "passed" || status === "auto" || status === "fixed",
            detail: String(row.detail || ""),
          };
        })
      : [],
  };
}

/** Auto must stay "auto" so Python measures the file. The A5 fallback is only a size until then. */
export function productIdForEngine(requestedId: string, resolvedId: string): string {
  return requestedId === "auto" ? "auto" : resolvedId;
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
    quickPrintScript(),
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
  if (options.editRequest) args.push("--edit-request", options.editRequest);
  if (options.replicateEdit) args.push("--replicate-edit");
  if (options.skipUpscale) args.push("--skip-upscale");
  for (const line of options.concerns || []) {
    if (String(line).trim()) args.push("--concern", String(line).slice(0, 400));
  }
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
    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, quickPrintKillMs());
    child.stdout.on("data", (chunk: Buffer) => {
      stdoutChunks.push(Buffer.from(chunk));
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderrChunks.push(Buffer.from(chunk));
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", () => {
      clearTimeout(timer);
      const stdout = decodeChildText(stdoutChunks);
      const stderr = decodeChildText(stderrChunks);
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
        let bytes = 0;
        try { bytes = fs.statSync(inputPath).size; } catch { bytes = 0; }
        resolve(deadChildResult(timedOut, bytes));
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
  const checks: AuditCheck[] = result.decisions.map((message) => ({
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
  for (const extra of result.extraChecks || []) {
    checks.push({
      name: extra.name || extra.id || "Extra check",
      passed: extra.pass === true,
      message: extra.message || extra.detail || "",
      autoFixed: extra.autoFixed === true,
      id: extra.id,
      status: extra.status,
    });
  }
  const { pressEngine, ...quickPrint } = result;
  const reused = publishQuickReuse(jobId, result);
  const audit = {
    checks,
    overallPassed: result.light === "green" && enginePassed,
    fixesApplied: result.light === "red" ? 0 : checks.length,
    complianceReport: result.decisions.join(" "),
    compiledPdfPath: result.pressPath || undefined,
    proofPath: result.proofPng || undefined,
    proofPaths: result.proofPaths.length ? result.proofPaths : undefined,
    pageCount: result.pageCount || undefined,
    proofPageCount: result.pageCount || undefined,
    quickPrint: { ...quickPrint, approved: false },
    pressEngine,
    order: {
      widthMm: result.trimWidthMm || null,
      heightMm: result.trimHeightMm || null,
      explicitSize: Boolean(result.trimWidthMm && result.trimHeightMm),
      sides: null,
      pageCount: result.pageCount || null,
      finishes: [],
      headToFoot: false,
    },
    ...reused,
  } as AuditResults;
  await storage.updateJob(jobId, {
    status: "complete",
    correctedPath: result.pressPath || undefined,
    completedAt: new Date(),
    auditResults: audit,
    productWidthMm: result.trimWidthMm || null,
    productHeightMm: result.trimHeightMm || null,
    orderedPageCount: result.pageCount || null,
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

export interface IntakeItem {
  id: string;
  num: string;
  name: string;
  detail: string;
  status: string;
  severity: "red" | "amber" | "green";
}

function pythonJson(script: string, args: string[], killMs: number): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, [script, ...args], {
      cwd: process.cwd(),
      env: pythonChildEnv(),
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
    }, killMs);
    child.stdout.on("data", (chunk: Buffer) => {
      stdoutChunks.push(Buffer.from(chunk));
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderrChunks.push(Buffer.from(chunk));
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      const stdout = decodeChildText(stdoutChunks);
      const stderr = decodeChildText(stderrChunks);
      const line = stdout.split(/\r?\n/).map((row) => row.trim()).filter((row) => row.startsWith("{")).pop();
      if (!line) {
        reject(new Error(stderr.trim().slice(0, 240) || "The check did not finish."));
        return;
      }
      try {
        const parsed = JSON.parse(line) as Record<string, unknown>;
        if (code && parsed.ok === false) {
          reject(new Error(String(parsed.error || "The bleed could not be changed.")));
          return;
        }
        resolve(parsed);
      } catch (error) {
        reject(error);
      }
    });
  });
}

export async function runIntakeCheck(inputPath: string, trimW: number, trimH: number): Promise<{ items: IntakeItem[]; red: number; amber: number; green: number }> {
  const script = path.join(process.cwd(), "server", "intake_check.py");
  const parsed = await pythonJson(script, ["--input", inputPath, "--trim-w", String(trimW), "--trim-h", String(trimH)], 120_000);
  const items = Array.isArray(parsed.items) ? parsed.items.map((item) => {
    const row = item && typeof item === "object" ? item as Record<string, unknown> : {};
    const severity: "red" | "amber" | "green" = row.severity === "red" || row.severity === "amber" ? row.severity : "green";
    return {
      id: String(row.id || ""),
      num: String(row.num || ""),
      name: String(row.name || ""),
      detail: String(row.detail || ""),
      status: String(row.status || ""),
      severity,
    };
  }) : [];
  return {
    items,
    red: Number(parsed.red) || items.filter((item) => item.severity === "red").length,
    amber: Number(parsed.amber) || items.filter((item) => item.severity === "amber").length,
    green: Number(parsed.green) || items.filter((item) => item.severity === "green").length,
  };
}

export async function runBleedReapply(pressPath: string, strategy: string, trimW: number, trimH: number, outputDir: string): Promise<{ bleedNote: string; method: string }> {
  const script = path.join(process.cwd(), "server", "bleed_reapply.py");
  const parsed = await pythonJson(script, [
    "--press", pressPath,
    "--strategy", strategy,
    "--trim-w", String(trimW),
    "--trim-h", String(trimH),
    "--output-dir", outputDir,
  ], 180_000);
  if (parsed.ok === false) throw new Error(String(parsed.error || "The bleed could not be changed."));
  return { bleedNote: String(parsed.bleedNote || ""), method: String(parsed.method || strategy) };
}
