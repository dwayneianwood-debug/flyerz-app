/**
 * Node wrapper for Adobe Illustrator / EPS intake.
 * PDF-compatible .ai files stay as PDF bytes. PostScript .ai and .eps are
 * distilled by server/illustrator_intake.py before the normal PDF pipeline.
 */
import { spawnSync } from "child_process";
import path from "path";
import { getFlyerzTempRoot } from "./envPaths";

const PYTHON_BIN = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");
const SCRIPT = path.join(process.cwd(), "server", "illustrator_intake.py");

export const ILLUSTRATOR_RESAVE_MESSAGE =
  'This Illustrator file can\'t be read. It was saved without PDF compatibility, ' +
  'or it has no PostScript artwork we can open. In Adobe Illustrator choose ' +
  'File > Save As > Illustrator and tick "Create PDF Compatible File", ' +
  "or export as PDF/X-1a, then upload that file.";

export class IllustratorIntakeError extends Error {
  code = "illustrator_resave";
  constructor(message: string = ILLUSTRATOR_RESAVE_MESSAGE) {
    super(message);
    this.name = "IllustratorIntakeError";
  }
}

export const ALLOWED_UPLOAD_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".docx", ".pptx", ".ai", ".eps"];
export const PRINT_TOOL_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".ai", ".eps"];

export const INVALID_UPLOAD_MESSAGE =
  "Invalid file type. Only PDF, JPG, PNG, DOCX, PPTX, AI, and EPS are allowed.";

export const PRINT_TOOL_REJECTION =
  "Only PDF, AI, EPS, JPG, and PNG files are supported.";

const GENERIC_MIME = new Set([
  "",
  "application/octet-stream",
  "binary/octet-stream",
  "application/binary",
  "application/x-download",
  "application/download",
]);

const MIME_BY_EXT: Record<string, string[]> = {
  ".pdf": ["application/pdf", "application/x-pdf"],
  ".jpg": ["image/jpeg", "image/jpg", "image/pjpeg"],
  ".jpeg": ["image/jpeg", "image/jpg", "image/pjpeg"],
  ".png": ["image/png", "image/x-png"],
  ".docx": [
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/zip",
  ],
  ".pptx": [
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/zip",
  ],
  ".ai": [
    "application/pdf",
    "application/postscript",
    "application/illustrator",
    "application/vnd.adobe.illustrator",
    "application/x-illustrator",
  ],
  ".eps": [
    "application/postscript",
    "application/eps",
    "application/x-eps",
    "image/eps",
    "image/x-eps",
    "application/x-postscript",
  ],
};

export function isIllustratorName(filename: string): boolean {
  const ext = path.extname(filename || "").toLowerCase();
  return ext === ".ai" || ext === ".eps";
}

export function isIllustratorType(fileType: string | null | undefined): boolean {
  const t = (fileType || "").toLowerCase();
  return t === "ai" || t === "eps";
}

export function isAllowedUpload(filename: string, mimetype?: string | null): boolean {
  const ext = path.extname(filename || "").toLowerCase();
  if (!ALLOWED_UPLOAD_EXTENSIONS.includes(ext)) return false;
  const mime = (mimetype || "").split(";")[0].trim().toLowerCase();
  if (GENERIC_MIME.has(mime)) return true;
  return (MIME_BY_EXT[ext] || []).includes(mime);
}

export function isAllowedPrintTool(filename: string, mimetype?: string | null): boolean {
  const ext = path.extname(filename || "").toLowerCase();
  if (!PRINT_TOOL_EXTENSIONS.includes(ext)) return false;
  return isAllowedUpload(filename, mimetype);
}

function pythonEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
  if (!env.FAI_TEMP_DIR?.trim()) {
    env.FAI_TEMP_DIR = getFlyerzTempRoot();
  }
  return env;
}

function runIntake(args: string[]): any {
  const proc = spawnSync(PYTHON_BIN, [SCRIPT, ...args], {
    cwd: process.cwd(),
    env: pythonEnv(),
    encoding: "utf8",
    timeout: 120_000,
    maxBuffer: 20 * 1024 * 1024,
  });

  const stdout = (proc.stdout || "").trim();
  let parsed: any = null;
  try {
    parsed = stdout ? JSON.parse(stdout) : null;
  } catch {
    parsed = null;
  }

  if (proc.error || proc.status !== 0 || !parsed || parsed.success === false) {
    const message = typeof parsed?.error === "string" && parsed.error.trim()
      ? parsed.error
      : ILLUSTRATOR_RESAVE_MESSAGE;
    throw new IllustratorIntakeError(message);
  }
  return parsed;
}

export interface PreparedIllustrator {
  kind: "pdf" | "postscript";
  pageCount: number;
  outputPath: string;
  preservedVectors: boolean;
  epsCrop?: boolean;
  sourceExt?: string;
}

export function prepareIllustratorFile(inputPath: string, originalName: string): PreparedIllustrator {
  const ext = path.extname(originalName || inputPath).toLowerCase() || ".ai";
  return runIntake(["prepare", inputPath, ext]);
}

export function extractIllustratorPage(inputPath: string, outputPath: string, pageIndex: number): void {
  runIntake(["extract", inputPath, outputPath, String(pageIndex)]);
}

export function renderIllustratorPreview(
  pdfPath: string,
  pngPath: string,
  pageIndex: number,
): { width: number; height: number; pageCount: number; page: number } {
  return runIntake(["preview", pdfPath, pngPath, String(pageIndex)]);
}

export interface IllustratorAuditCheck {
  id?: string;
  name: string;
  passed: boolean;
  message: string;
  details?: string;
  severity?: string;
  autoFixed?: boolean;
}

export function auditIllustratorFile(pdfPath: string): IllustratorAuditCheck[] {
  const parsed = runIntake(["audit", pdfPath]);
  return Array.isArray(parsed.checks) ? parsed.checks : [];
}
