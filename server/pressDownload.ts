/** Where a press PDF lives for the download routes. */
import path from "node:path";

export const PRESS_FILE_MISSING = "There isn't a press-ready PDF for this job yet.";

export interface PressJobFile {
  id?: number | string | null;
  correctedPath?: string | null;
  auditResults?: {
    compiledPdfPath?: string;
    quickPrint?: { pressPath?: string };
  } | null;
}

function isPdf(filePath: string): boolean {
  return path.extname(filePath).toLowerCase() === ".pdf";
}

/** Stored Windows paths use backslashes. Relative ones resolve under the app root. */
export function normalizeStoredPath(value: string, baseDir: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  const slashed = trimmed.replace(/\\/g, "/");
  const drive = slashed.match(/^[A-Za-z]:\/(.*)$/);
  let relative = slashed;
  if (drive) {
    const rest = drive[1];
    const at = rest.toLowerCase().indexOf("uploads/");
    relative = at >= 0 ? rest.slice(at) : rest;
    if (process.platform === "win32" && at < 0) return path.normalize(trimmed);
  }
  if (relative.startsWith("/")) return path.normalize(relative);
  return path.resolve(baseDir, ...relative.split("/").filter(Boolean));
}

export function pressFileCandidates(job: PressJobFile, baseDir: string = process.cwd()): string[] {
  const audit = job.auditResults;
  const found: string[] = [];
  const push = (value?: string | null) => {
    const raw = (value || "").trim();
    if (!raw || !isPdf(raw.replace(/\\/g, "/"))) return;
    const filePath = normalizeStoredPath(raw, baseDir);
    if (filePath && !found.includes(filePath)) found.push(filePath);
  };
  // The database file is the press PDF for every job type. Smaller compiled
  // or quick-print copies are only a fallback when that file is missing.
  push(job.correctedPath);
  push(audit?.quickPrint?.pressPath);
  push(audit?.compiledPdfPath);
  const id = String(job.id ?? "").trim();
  if (/^\d+$/.test(id)) {
    push(path.join(baseDir, "uploads", "quick-print", id, "press.pdf"));
  }
  return found;
}

export function firstExistingPressFile(
  job: PressJobFile,
  isSafe: (filePath: string) => boolean,
  exists: (filePath: string) => boolean,
  baseDir: string = process.cwd(),
): string | null {
  for (const candidate of pressFileCandidates(job, baseDir)) {
    if (isSafe(candidate) && exists(candidate)) return candidate;
  }
  return null;
}
