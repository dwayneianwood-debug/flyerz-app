/**
 * Removes finished jobs from before today (Africa/Johannesburg).
 * Today's artwork stays. A job that is still processing stays.
 * Files are deleted only inside this app's uploads and jobs folders.
 */
import fs from "fs";
import path from "path";
import type { Express, Request, Response } from "express";

export const JOHANNESBURG = "Africa/Johannesburg";
export const CLEANUP_HOUR_MS = 60 * 60 * 1000;
const MIDNIGHT_DELAY_MS = 5 * 60 * 1000;

export interface JobCleanupSettings {
  enabled: boolean;
}

export interface CleanupJob {
  id: number;
  filename: string;
  status: string;
  uploadedAt: string | Date;
  originalPath?: string | null;
  correctedPath?: string | null;
  auditResults?: unknown;
}

export interface CleanupReport {
  enabled: boolean;
  backupCreated: boolean;
  removedJobs: number;
  keptToday: number;
  keptProcessing: number;
  filesRemoved: number;
  bytesRemoved: number;
  skippedOutside: string[];
}

export function jobCleanupSettingsPath(): string {
  return process.env.JOB_CLEANUP_SETTINGS_PATH
    ? path.resolve(process.env.JOB_CLEANUP_SETTINGS_PATH)
    : path.join(process.cwd(), "data", "job-cleanup-settings.json");
}

export function readJobCleanupSettings(filePath = jobCleanupSettingsPath()): JobCleanupSettings {
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, "utf8")) as { enabled?: unknown };
    if (parsed && parsed.enabled === false) return { enabled: false };
    return { enabled: true };
  } catch {
    return { enabled: true };
  }
}

export function writeJobCleanupSettings(enabled: boolean, filePath = jobCleanupSettingsPath()): JobCleanupSettings {
  const next = { enabled: enabled === true };
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(next, null, 2));
  return next;
}

/** SQLite datetime('now') is UTC and has no zone suffix. */
export function parseStoredTime(value: string | Date): Date {
  if (value instanceof Date) return value;
  const text = String(value || "").trim();
  if (!text) return new Date(NaN);
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text)) return new Date(text);
  const match = text.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/);
  if (match) return new Date(`${match[1]}T${match[2]}Z`);
  return new Date(text);
}

export function johannesburgDate(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: JOHANNESBURG,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export function startOfTodayJohannesburg(now: Date): Date {
  return new Date(`${johannesburgDate(now)}T00:00:00+02:00`);
}

export function msUntilJustAfterMidnight(now: Date): number {
  const start = startOfTodayJohannesburg(now);
  let target = start.getTime() + MIDNIGHT_DELAY_MS;
  if (target <= now.getTime()) target += 24 * 60 * 60 * 1000;
  return target - now.getTime();
}

export function planCleanup(jobs: CleanupJob[], now: Date): {
  remove: CleanupJob[];
  keepToday: CleanupJob[];
  keepProcessing: CleanupJob[];
} {
  const start = startOfTodayJohannesburg(now);
  const remove: CleanupJob[] = [];
  const keepToday: CleanupJob[] = [];
  const keepProcessing: CleanupJob[] = [];
  for (const job of jobs) {
    if (String(job.status) === "processing") {
      keepProcessing.push(job);
      continue;
    }
    const uploaded = parseStoredTime(job.uploadedAt);
    if (Number.isNaN(uploaded.getTime()) || uploaded.getTime() >= start.getTime()) {
      keepToday.push(job);
      continue;
    }
    remove.push(job);
  }
  return { remove, keepToday, keepProcessing };
}

export function pathIsInside(filePath: string, root: string): boolean {
  const rootResolved = path.resolve(root);
  const target = path.resolve(filePath);
  const rel = path.relative(rootResolved, target);
  if (!rel || rel === "") return false;
  if (rel.startsWith("..") || path.isAbsolute(rel)) return false;
  return true;
}

function looksLikePath(value: string): boolean {
  if (!value || value.length > 500) return false;
  return value.includes("/") || value.includes("\\");
}

function collectPathStrings(value: unknown, out: string[]): void {
  if (typeof value === "string") {
    if (looksLikePath(value)) out.push(value);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectPathStrings(item, out);
    return;
  }
  if (value && typeof value === "object") {
    for (const item of Object.values(value as Record<string, unknown>)) collectPathStrings(item, out);
  }
}

export function candidatePaths(job: CleanupJob, uploadsRoot: string): string[] {
  const found: string[] = [];
  if (job.originalPath) found.push(job.originalPath);
  if (job.correctedPath) found.push(job.correctedPath);
  collectPathStrings(job.auditResults, found);
  const id = String(job.id);
  found.push(path.join(uploadsRoot, "quick-print", id));
  found.push(path.join(uploadsRoot, "thumbs", `${id}.jpg`));
  try {
    const names = fs.readdirSync(uploadsRoot);
    for (const name of names) {
      if (name.startsWith(`${id}_`)) found.push(path.join(uploadsRoot, name));
    }
  } catch {
    /* uploads folder may not exist yet */
  }
  return found;
}

export interface SafeRoots {
  allowed: string[];
  protected: string[];
}

function realInside(filePath: string, roots: SafeRoots): string | null {
  let stat: fs.Stats;
  try {
    stat = fs.lstatSync(filePath);
  } catch {
    return null;
  }
  if (stat.isSymbolicLink()) {
    const linkPath = path.resolve(filePath);
    if (!roots.allowed.some((root) => pathIsInside(linkPath, root))) return null;
    if (roots.protected.some((root) => linkPath === path.resolve(root) || pathIsInside(linkPath, root))) return null;
    return linkPath;
  }
  let real: string;
  try {
    real = fs.realpathSync(filePath);
  } catch {
    return null;
  }
  if (!roots.allowed.some((root) => pathIsInside(real, root))) return null;
  if (roots.protected.some((root) => real === path.resolve(root) || pathIsInside(real, root))) return null;
  return real;
}

function filesInside(target: string, roots: SafeRoots, skipped: string[]): string[] {
  const real = realInside(target, roots);
  if (!real) {
    if (fs.existsSync(target)) skipped.push(path.resolve(target));
    return [];
  }
  let stat: fs.Stats;
  try {
    stat = fs.lstatSync(target);
  } catch {
    return [];
  }
  if (stat.isSymbolicLink()) return [target];
  if (stat.isDirectory()) {
    const files: string[] = [];
    let names: string[] = [];
    try {
      names = fs.readdirSync(target);
    } catch {
      return [];
    }
    for (const name of names) {
      files.push(...filesInside(path.join(target, name), roots, skipped));
    }
    return files;
  }
  if (stat.isFile()) return [target];
  return [];
}

export function bytesLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface CleanupDeps {
  now?: Date;
  jobs: CleanupJob[];
  uploadsRoot: string;
  jobsRoot: string;
  backupDir: string;
  databasePath: string;
  enabled: boolean;
  reason?: "startup" | "midnight" | "hourly";
  backupDatabase: (dest: string) => void;
  deleteJob: (id: number) => void | Promise<void>;
  log?: (line: string) => void;
}

export function backupDatabaseFile(exec: (sql: string) => void, dest: string): boolean {
  if (fs.existsSync(dest) && fs.statSync(dest).size > 0) return false;
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const literal = dest.replace(/\\/g, "/").replace(/'/g, "''");
  exec(`VACUUM INTO '${literal}'`);
  return true;
}

export async function runJobCleanup(deps: CleanupDeps): Promise<CleanupReport> {
  const log = deps.log || ((line) => console.log(line));
  const now = deps.now || new Date();
  const report: CleanupReport = {
    enabled: deps.enabled,
    backupCreated: false,
    removedJobs: 0,
    keptToday: 0,
    keptProcessing: 0,
    filesRemoved: 0,
    bytesRemoved: 0,
    skippedOutside: [],
  };
  if (!deps.enabled) {
    if (deps.reason === "startup") log("[Job cleanup] Overnight cleanup is off. Old jobs will be kept.");
    return report;
  }

  const backupFile = path.join(deps.backupDir, "flyerz-before-first-cleanup.sqlite");
  const already = fs.existsSync(backupFile) && fs.statSync(backupFile).size > 0;
  if (!already) {
    deps.backupDatabase(backupFile);
    report.backupCreated = true;
    log(`[Job cleanup] Saved one database copy to ${backupFile}.`);
  }

  const plan = planCleanup(deps.jobs, now);
  report.keptToday = plan.keepToday.length;
  report.keptProcessing = plan.keepProcessing.length;
  const roots: SafeRoots = {
    allowed: [deps.uploadsRoot, deps.jobsRoot],
    protected: [deps.backupDir, path.dirname(deps.databasePath), deps.databasePath],
  };

  if (deps.reason !== "hourly") {
    for (const job of plan.keepProcessing) {
      log(`[Job cleanup] Kept job ${job.id} (${job.filename}) because it is still processing.`);
    }
  }

  for (const job of plan.remove) {
    const skipped: string[] = [];
    const seen = new Set<string>();
    const files: string[] = [];
    for (const candidate of candidatePaths(job, deps.uploadsRoot)) {
      for (const file of filesInside(candidate, roots, skipped)) {
        let key = file;
        try {
          key = fs.realpathSync(file);
        } catch {
          key = path.resolve(file);
        }
        if (seen.has(key)) continue;
        seen.add(key);
        files.push(file);
      }
    }
    let bytes = 0;
    let removed = 0;
    let failed = false;
    const dirs = new Set<string>();
    for (const file of files) {
      try {
        const stat = fs.lstatSync(file);
        if (stat.isSymbolicLink()) {
          fs.unlinkSync(file);
          removed += 1;
          continue;
        }
        if (!stat.isFile()) continue;
        bytes += stat.size;
        fs.unlinkSync(file);
        removed += 1;
        dirs.add(path.dirname(file));
      } catch (error) {
        failed = true;
        const message = error instanceof Error ? error.message : String(error);
        log(`[Job cleanup] Could not remove a file for job ${job.id}: ${message}`);
      }
    }
    for (const dir of Array.from(dirs)) {
      removeEmptyParents(dir, roots);
    }
    const quickDir = path.join(deps.uploadsRoot, "quick-print", String(job.id));
    removeEmptyParents(quickDir, roots);
    if (failed) {
      log(`[Job cleanup] Job ${job.id} (${job.filename}) is still in the list because a file could not be removed.`);
      report.skippedOutside.push(...skipped);
      continue;
    }
    await deps.deleteJob(job.id);
    report.removedJobs += 1;
    report.filesRemoved += removed;
    report.bytesRemoved += bytes;
    report.skippedOutside.push(...skipped);
    const outsideNote = skipped.length ? ` Left ${skipped.length} file${skipped.length === 1 ? "" : "s"} outside the app folders.` : "";
    log(`[Job cleanup] Removed job ${job.id} (${job.filename}): ${removed} file${removed === 1 ? "" : "s"}, ${bytesLabel(bytes)}.${outsideNote}`);
  }

  if (report.removedJobs > 0 || deps.reason === "startup") {
    log(
      `[Job cleanup] Removed ${report.removedJobs} job${report.removedJobs === 1 ? "" : "s"} from before today (${bytesLabel(report.bytesRemoved)}). Kept ${report.keptToday} from today and ${report.keptProcessing} still processing.`,
    );
  }
  return report;
}

function removeEmptyParents(start: string, roots: SafeRoots): void {
  let current = path.resolve(start);
  for (let i = 0; i < 6; i++) {
    if (!roots.allowed.some((root) => pathIsInside(current, root))) return;
    if (roots.protected.some((root) => current === path.resolve(root) || pathIsInside(current, root))) return;
    try {
      if (!fs.statSync(current).isDirectory()) return;
      if (fs.readdirSync(current).length > 0) return;
      fs.rmdirSync(current);
    } catch {
      return;
    }
    current = path.dirname(current);
  }
}

export function registerJobCleanupRoutes(app: Express): void {
  app.get("/api/job-cleanup/settings", (_req: Request, res: Response) => {
    res.json(readJobCleanupSettings());
  });
  app.post("/api/job-cleanup/settings", (req: Request, res: Response) => {
    const enabled = req.body?.enabled !== false;
    res.json(writeJobCleanupSettings(enabled));
  });
}

let started = false;
let hourly: ReturnType<typeof setInterval> | null = null;
let midnight: ReturnType<typeof setTimeout> | null = null;

export function startJobCleanup(): void {
  if (started) return;
  started = true;
  const run = (reason: "startup" | "midnight" | "hourly") => {
    runStoredJobCleanup(reason).catch((error) => {
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`[Job cleanup] Run failed: ${message}`);
    });
  };
  console.log("[Job cleanup] Overnight cleanup is scheduled. It runs at startup, just after midnight Johannesburg time, and every hour.");
  run("startup");
  hourly = setInterval(() => run("hourly"), CLEANUP_HOUR_MS);
  if (typeof hourly.unref === "function") hourly.unref();
  const armMidnight = () => {
    const delay = msUntilJustAfterMidnight(new Date());
    midnight = setTimeout(() => {
      run("midnight");
      armMidnight();
    }, delay);
    if (typeof midnight.unref === "function") midnight.unref();
  };
  armMidnight();
}

export async function runStoredJobCleanup(reason: "startup" | "midnight" | "hourly" = "hourly"): Promise<CleanupReport> {
  const { storage, databaseFilePath, backupDatabaseTo } = await import("./storage.ts");
  const settings = readJobCleanupSettings();
  const jobs = await storage.getJobs();
  return runJobCleanup({
    jobs,
    uploadsRoot: path.join(process.cwd(), "uploads"),
    jobsRoot: path.join(process.cwd(), "jobs"),
    backupDir: path.join(process.cwd(), "data", "backups"),
    databasePath: databaseFilePath(),
    enabled: settings.enabled,
    reason,
    backupDatabase: (dest) => {
      backupDatabaseTo(dest);
    },
    deleteJob: (id) => storage.deleteJob(id),
  });
}
