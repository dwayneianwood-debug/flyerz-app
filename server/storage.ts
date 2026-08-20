import fs from "fs";
import path from "path";
import { Buffer } from "node:buffer";
import { DatabaseSync } from "node:sqlite";
import type { FileJobResponse, CreateFileJobRequest, UpdateFileJobRequest } from "@shared/schema";

export interface IStorage {
  getJobs(): Promise<FileJobResponse[]>;
  getJob(id: number): Promise<FileJobResponse | undefined>;
  createJob(job: CreateFileJobRequest): Promise<FileJobResponse>;
  updateJob(id: number, updates: UpdateFileJobRequest): Promise<FileJobResponse>;
  deleteJob(id: number): Promise<void>;
}

const dataDir = path.join(process.cwd(), "data");
const dbPath = path.join(dataDir, "flyerz.sqlite");
if (!fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true });
}

const sqlite = new DatabaseSync(dbPath);
sqlite.exec(`
  CREATE TABLE IF NOT EXISTS file_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    corrected_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    file_size INTEGER NOT NULL,
    file_type TEXT NOT NULL,
    audit_results TEXT,
    error_message TEXT
  )
`);

type JobRow = {
  id: number;
  filename: string;
  original_path: string;
  corrected_path: string | null;
  status: string;
  uploaded_at: string;
  completed_at: string | null;
  file_size: number;
  file_type: string;
  /** SQLite TEXT; driver may return string, Buffer, or Uint8Array */
  audit_results: string | Buffer | Uint8Array | null;
  error_message: string | null;
};

/** Coerce SQLite column value to UTF-8 text before JSON.parse. */
function auditColumnToString(raw: unknown): string | null {
  if (raw == null) return null;
  if (typeof raw === "string") return raw;
  if (Buffer.isBuffer(raw)) return raw.toString("utf8");
  if (raw instanceof Uint8Array) return Buffer.from(raw).toString("utf8");
  return String(raw);
}

/** Unwrap values that were stored as JSON strings (Postgres JSONB → SQLite TEXT migration, double stringify, etc.). */
function unfoldJsonValue(val: unknown): unknown {
  let cur: unknown = val;
  for (let i = 0; i < 8 && typeof cur === "string"; i++) {
    const s = cur.trim().replace(/^\uFEFF/, "");
    if (!s) break;
    const c0 = s[0];
    if (c0 !== "{" && c0 !== "[") break;
    try {
      cur = JSON.parse(s);
    } catch {
      break;
    }
  }
  return cur;
}

const NESTED_JSON_KEYS = [
  "savedBleedOptions",
  "bleedOptions",
  "bleedVariants",
  "aiEnhancements",
  "artworkSize",
  "compileAuditReport",
  "checks",
] as const;

/**
 * SQLite stores `audit_results` as TEXT. Some pipelines persist nested objects
 * (e.g. savedBleedOptions) as an additional JSON.stringify — then .targetWidth
 * is undefined until this nested string is parsed.
 */
function normalizeAuditResultsObject(audit: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...audit };
  for (const key of NESTED_JSON_KEYS) {
    if (!(key in out)) continue;
    out[key] = unfoldJsonValue(out[key]);
  }
  if (Array.isArray(out.checks)) {
    out.checks = out.checks.map((item) => unfoldJsonValue(item));
  }
  return out;
}

/** Emergency defaults — proves pipeline when DB/JSON is broken. */
export const HARDCODE_DEFAULT_TRIM_W_MM = 148;
export const HARDCODE_DEFAULT_TRIM_H_MM = 210;

/**
 * Force savedBleedOptions to a plain object with valid targetWidth/targetHeight (mm).
 * Manually parses string layers; defaults to A5 if still missing.
 */
export function coerceSavedBleedOptionsFromDb(raw: unknown): Record<string, any> {
  let cur: unknown = raw;
  cur = unfoldJsonValue(cur);
  let o: Record<string, any>;
  if (cur && typeof cur === "object" && !Array.isArray(cur)) {
    o = { ...(cur as Record<string, any>) };
  } else if (typeof cur === "string") {
    const trimmed = cur.trim().replace(/^\uFEFF/, "");
    if ((trimmed.startsWith("{") || trimmed.startsWith("[")) && trimmed.length > 1) {
      try {
        const once = JSON.parse(trimmed);
        cur = unfoldJsonValue(once);
        if (cur && typeof cur === "object" && !Array.isArray(cur)) {
          o = { ...(cur as Record<string, any>) };
        } else {
          o = {};
        }
      } catch {
        o = {};
      }
    } else {
      o = {};
    }
  } else {
    o = {};
  }

  let tw = Number(o.targetWidth);
  let th = Number(o.targetHeight);
  const hadValidPair =
    Number.isFinite(tw) && tw > 0 && Number.isFinite(th) && th > 0;
  if (!Number.isFinite(tw) || tw <= 0) tw = HARDCODE_DEFAULT_TRIM_W_MM;
  if (!Number.isFinite(th) || th <= 0) th = HARDCODE_DEFAULT_TRIM_H_MM;
  if (!hadValidPair) {
    console.warn(
      `[FAI][storage] Hard-coded trim defaults applied: ${tw}×${th}mm (savedBleedOptions was ${raw === undefined ? "missing" : typeof raw === "string" ? "string" : "non-numeric targets"})`,
    );
  }
  return { ...o, targetWidth: tw, targetHeight: th };
}

/** True when crop_box is the No Crop Needed full-page box (0,0,1,1) or is_no_crop. */
export function isFullPageNoCrop(opts: Record<string, any> | null | undefined): boolean {
  if (!opts) return false;
  if (opts.is_no_crop === true) return true;
  const cx = Number(opts.cropX);
  const cy = Number(opts.cropY);
  const cw = Number(opts.cropWidth);
  const ch = Number(opts.cropHeight);
  return cx === 0 && cy === 0 && cw === 1 && ch === 1;
}

/** User-drawn crop only — full-page no-crop must not be treated as a mockup-killer crop. */
export function isManualCropActive(opts: Record<string, any> | null | undefined): boolean {
  if (!opts) return false;
  if (isFullPageNoCrop(opts)) return false;
  const cw = Number(opts.cropWidth);
  const ch = Number(opts.cropHeight);
  return opts.cropX != null && opts.cropY != null && Number.isFinite(cw) && cw > 0 && Number.isFinite(ch) && ch > 0;
}

function parseAuditResultsColumn(raw: unknown): FileJobResponse["auditResults"] {
  const text = auditColumnToString(raw);
  if (text == null || text === "") return null;
  try {
    let v: unknown = JSON.parse(text.trim().replace(/^\uFEFF/, ""));
    v = unfoldJsonValue(v);
    if (v == null || typeof v !== "object" || Array.isArray(v)) return null;
    const normalized = normalizeAuditResultsObject(v as Record<string, unknown>);
    normalized.savedBleedOptions = coerceSavedBleedOptionsFromDb(normalized.savedBleedOptions);
    return normalized as unknown as FileJobResponse["auditResults"];
  } catch {
    return null;
  }
}

function mapRowToResponse(row: JobRow): FileJobResponse {
  return {
    id: row.id,
    filename: row.filename,
    originalPath: row.original_path,
    correctedPath: row.corrected_path,
    status: row.status as FileJobResponse["status"],
    uploadedAt: new Date(row.uploaded_at),
    completedAt: row.completed_at ? new Date(row.completed_at) : null,
    fileSize: row.file_size,
    fileType: row.file_type as FileJobResponse["fileType"],
    auditResults: parseAuditResultsColumn(row.audit_results as unknown),
    errorMessage: row.error_message,
  };
}

export class DatabaseStorage implements IStorage {
  async getJobs(): Promise<FileJobResponse[]> {
    const rows = sqlite
      .prepare(
        `SELECT id, filename, original_path, corrected_path, status, uploaded_at, completed_at, file_size, file_type, audit_results, error_message
         FROM file_jobs
         ORDER BY uploaded_at`,
      )
      .all() as JobRow[];
    return rows.map((row) => mapRowToResponse(row));
  }

  async getJob(id: number): Promise<FileJobResponse | undefined> {
    const row = sqlite
      .prepare(
        `SELECT id, filename, original_path, corrected_path, status, uploaded_at, completed_at, file_size, file_type, audit_results, error_message
         FROM file_jobs
         WHERE id = ?`,
      )
      .get(id) as JobRow | undefined;
    if (!row) return undefined;
    return mapRowToResponse(row);
  }

  async createJob(job: CreateFileJobRequest): Promise<FileJobResponse> {
    const result = sqlite
      .prepare(
        `INSERT INTO file_jobs (filename, original_path, file_size, file_type, status)
         VALUES (?, ?, ?, ?, 'pending')`,
      )
      .run(job.filename, job.originalPath, job.fileSize, job.fileType);
    const created = await this.getJob(Number(result.lastInsertRowid));
    if (!created) {
      throw new Error("Failed to create job");
    }
    return created;
  }

  async updateJob(id: number, updates: UpdateFileJobRequest): Promise<FileJobResponse> {
    const setClauses: string[] = [];
    const values: unknown[] = [];

    if (updates.status !== undefined) {
      setClauses.push("status = ?");
      values.push(updates.status);
    }
    if (updates.correctedPath !== undefined) {
      setClauses.push("corrected_path = ?");
      values.push(updates.correctedPath);
    }
    if (updates.completedAt !== undefined) {
      setClauses.push("completed_at = ?");
      values.push(updates.completedAt ? updates.completedAt.toISOString() : null);
    }
    if (updates.auditResults !== undefined) {
      setClauses.push("audit_results = ?");
      values.push(updates.auditResults ? JSON.stringify(updates.auditResults) : null);
    }
    if (updates.errorMessage !== undefined) {
      setClauses.push("error_message = ?");
      values.push(updates.errorMessage);
    }

    if (setClauses.length > 0) {
      values.push(id);
      sqlite
        .prepare(`UPDATE file_jobs SET ${setClauses.join(", ")} WHERE id = ?`)
        .run(...values);
    }

    const updated = await this.getJob(id);
    if (!updated) {
      throw new Error(`Job ${id} not found`);
    }
    return updated;
  }

  async deleteJob(id: number): Promise<void> {
    sqlite.prepare("DELETE FROM file_jobs WHERE id = ?").run(id);
  }
}

export const storage = new DatabaseStorage();
