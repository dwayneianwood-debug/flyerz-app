import fs from "fs";
import path from "path";
import { Buffer } from "node:buffer";
import { DatabaseSync } from "node:sqlite";
import type { FileJobResponse, CreateFileJobRequest, UpdateFileJobRequest, JobListItem, JobListPage, JobStatus } from "@shared/schema";

export interface IStorage {
  getJobs(): Promise<FileJobResponse[]>;
  getJob(id: number): Promise<FileJobResponse | undefined>;
  createJob(job: CreateFileJobRequest): Promise<FileJobResponse>;
  updateJob(id: number, updates: UpdateFileJobRequest): Promise<FileJobResponse>;
  deleteJob(id: number): Promise<void>;
}

const dataDir = path.join(process.cwd(), "data");
const dbPath = process.env.FLYERZ_DB_PATH
  ? path.resolve(process.env.FLYERZ_DB_PATH)
  : path.join(dataDir, "flyerz.sqlite");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });

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
sqlite.exec(`
  CREATE INDEX IF NOT EXISTS idx_file_jobs_uploaded_at ON file_jobs (uploaded_at);
  CREATE INDEX IF NOT EXISTS idx_file_jobs_status_uploaded_at ON file_jobs (status, uploaded_at);
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

export function passedFlag(value: unknown): boolean | null {
  if (value === true || value === 1 || value === "1" || value === "true") return true;
  if (value === false || value === 0 || value === "0" || value === "false") return false;
  return null;
}

type ListRow = {
  id: number;
  filename: string;
  status: string;
  uploaded_at: string;
  file_size: number;
  file_type: string;
  has_corrected: number;
  overall_passed: unknown;
  press_passed: unknown;
  quick_light: unknown;
};

export class DatabaseStorage implements IStorage {
  async countJobs(): Promise<number> {
    const row = sqlite.prepare("SELECT COUNT(*) AS n FROM file_jobs").get() as { n: number };
    return Number(row?.n || 0);
  }

  async listJobs(options: { limit: number; offset: number; status?: string; attention?: boolean }): Promise<JobListPage> {
    const limit = Math.min(100, Math.max(1, Math.floor(options.limit)));
    const offset = Math.max(0, Math.floor(options.offset));
    const status = options.status;
    const clauses: string[] = [];
    const args: unknown[] = [];
    if (status) {
      clauses.push("status = ?");
      args.push(status);
    }
    if (options.attention) {
      clauses.push("json_extract(audit_results, '$.quickPrint.light') IN ('amber', 'red')");
      clauses.push("COALESCE(json_extract(audit_results, '$.quickPrint.approved'), 0) != 1");
    }
    const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
    const totalRow = sqlite
      .prepare(`SELECT COUNT(*) AS n FROM file_jobs ${where}`)
      .get(...args) as { n: number };
    const rows = sqlite
      .prepare(
        `SELECT id, filename, status, uploaded_at, file_size, file_type,
                CASE WHEN corrected_path IS NOT NULL AND corrected_path != '' THEN 1 ELSE 0 END AS has_corrected,
                json_extract(audit_results, '$.overallPassed') AS overall_passed,
                json_extract(audit_results, '$.pressEngine.passed') AS press_passed,
                json_extract(audit_results, '$.quickPrint.light') AS quick_light
         FROM file_jobs
         ${where}
         ORDER BY uploaded_at DESC, id DESC
         LIMIT ? OFFSET ?`,
      )
      .all(...args, limit, offset) as ListRow[];
    const jobs: JobListItem[] = rows.map((row) => {
      const overallPassed = passedFlag(row.overall_passed);
      const pressPassed = passedFlag(row.press_passed);
      const uploaded = new Date(row.uploaded_at);
      const quickLight = row.quick_light === "green" || row.quick_light === "amber" || row.quick_light === "red"
        ? row.quick_light
        : null;
      return {
        id: row.id,
        filename: row.filename,
        status: row.status as JobStatus,
        uploadedAt: Number.isNaN(uploaded.getTime()) ? String(row.uploaded_at) : uploaded.toISOString(),
        fileSize: row.file_size,
        fileType: row.file_type,
        thumbnailUrl: null,
        overallPassed,
        hasCorrectedFile: row.has_corrected === 1,
        printReady: row.status === "complete" && overallPassed === true && pressPassed === true,
        quickLight,
      };
    });
    const total = Number(totalRow?.n || 0);
    return {
      jobs,
      total,
      limit,
      offset,
      hasMore: offset + jobs.length < total,
    };
  }

  async listQuickPrint(limit: number): Promise<{ id: number }[]> {
    const cap = Math.min(24, Math.max(1, Math.floor(limit)));
    return sqlite
      .prepare(
        `SELECT id FROM file_jobs
         WHERE json_extract(audit_results, '$.quickPrint.light') IN ('green', 'amber', 'red')
         ORDER BY uploaded_at DESC, id DESC
         LIMIT ?`,
      )
      .all(cap) as { id: number }[];
  }

  async getJobStatus(id: number): Promise<{ id: number; status: string; errorMessage: string | null } | undefined> {
    const row = sqlite
      .prepare("SELECT id, status, error_message FROM file_jobs WHERE id = ?")
      .get(id) as { id: number; status: string; error_message: string | null } | undefined;
    if (!row) return undefined;
    return { id: row.id, status: row.status, errorMessage: row.error_message };
  }

  async getJobAuditStamp(id: number): Promise<{ status: string; bytes: number } | undefined> {
    const row = sqlite
      .prepare("SELECT status, length(COALESCE(audit_results, '')) AS bytes FROM file_jobs WHERE id = ?")
      .get(id) as { status: string; bytes: number } | undefined;
    return row;
  }

  async getCompiledStrategy(id: number): Promise<string | null> {
    const row = sqlite
      .prepare("SELECT json_extract(audit_results, '$.compiledStrategy') AS strategy FROM file_jobs WHERE id = ?")
      .get(id) as { strategy: string | null } | undefined;
    if (!row || row.strategy == null || row.strategy === "") return null;
    return String(row.strategy);
  }

  listIndexNames(): string[] {
    const rows = sqlite
      .prepare("SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")
      .all() as { name: string }[];
    return rows.map((row) => row.name);
  }

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
