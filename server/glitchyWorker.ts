import { spawn } from "child_process";

/**
 * Guarded, event-driven background worker for autonomous Glitchy fixes.
 *
 * Rather than a blind polling loop, work is triggered only when real feedback
 * is enqueued, and every trigger passes through four safety guards so a burst
 * of reports (or duplicate submissions) cannot spawn a runaway storm of Cloud
 * Agents:
 *   - enabled flag  : the whole worker is inert unless explicitly turned on.
 *   - de-duplication: identical feedback (same jobId + text) is ignored while
 *                     queued/in-flight and for a cooldown window afterwards.
 *   - rate cap      : at most one trigger per `minIntervalMs`.
 *   - concurrency   : at most `maxConcurrent` triggers in flight at once.
 *   - queue cap     : pending items above `maxQueue` are rejected.
 *
 * The actual trigger is injected so the guard logic stays pure and testable.
 */

export interface GlitchyFeedback {
  userFeedback: string;
  jobId?: number | string | null;
  cropBox?: unknown;
  gsLogs?: string;
  pageState?: Record<string, unknown> | null;
}

export type EnqueueReason = "disabled" | "empty" | "duplicate" | "queue_full";

export interface EnqueueResult {
  accepted: boolean;
  reason?: EnqueueReason;
  pending: number;
  inFlight: number;
}

export interface GlitchyWorkerOptions {
  enabled?: boolean;
  minIntervalMs?: number;
  maxConcurrent?: number;
  maxQueue?: number;
  dedupeWindowMs?: number;
  trigger: (item: GlitchyFeedback) => Promise<void> | void;
  now?: () => number;
  schedule?: (cb: () => void, ms: number) => void;
  logger?: (msg: string) => void;
}

export function glitchyDedupeKey(item: GlitchyFeedback): string {
  const job =
    item.jobId === undefined || item.jobId === null ? "" : String(item.jobId);
  const text = (item.userFeedback || "").trim().replace(/\s+/g, " ").toLowerCase();
  return job + "::" + text;
}

export class GlitchyWorker {
  private readonly enabled: boolean;
  private readonly minIntervalMs: number;
  private readonly maxConcurrent: number;
  private readonly maxQueue: number;
  private readonly dedupeWindowMs: number;
  private readonly triggerFn: (item: GlitchyFeedback) => Promise<void> | void;
  private readonly nowFn: () => number;
  private readonly scheduleFn: (cb: () => void, ms: number) => void;
  private readonly log: (msg: string) => void;

  private readonly queue: GlitchyFeedback[] = [];
  private inFlightCount = 0;
  private lastTriggerAt = Number.NEGATIVE_INFINITY;
  /** Keys queued or currently in-flight (blocks duplicates while active). */
  private readonly activeKeys = new Set<string>();
  /** Key -> last-accepted timestamp (blocks re-triggering during cooldown). */
  private readonly recentKeys = new Map<string, number>();
  private drainScheduled = false;

  constructor(opts: GlitchyWorkerOptions) {
    this.enabled = opts.enabled ?? false;
    this.minIntervalMs = Math.max(0, opts.minIntervalMs ?? 30_000);
    this.maxConcurrent = Math.max(1, opts.maxConcurrent ?? 1);
    this.maxQueue = Math.max(1, opts.maxQueue ?? 50);
    this.dedupeWindowMs = Math.max(0, opts.dedupeWindowMs ?? 5 * 60_000);
    this.triggerFn = opts.trigger;
    this.nowFn = opts.now ?? Date.now;
    this.scheduleFn =
      opts.schedule ?? ((cb, ms) => { setTimeout(cb, ms); });
    this.log = opts.logger ?? (() => {});
  }

  get pending(): number {
    return this.queue.length;
  }

  get inFlight(): number {
    return this.inFlightCount;
  }

  get isEnabled(): boolean {
    return this.enabled;
  }

  enqueue(item: GlitchyFeedback): EnqueueResult {
    if (!this.enabled) return this.result(false, "disabled");
    if (!(item.userFeedback || "").trim()) return this.result(false, "empty");

    const key = glitchyDedupeKey(item);
    const now = this.nowFn();
    this.pruneRecent(now);

    if (this.activeKeys.has(key)) return this.result(false, "duplicate");
    const seen = this.recentKeys.get(key);
    if (seen !== undefined && now - seen < this.dedupeWindowMs) {
      return this.result(false, "duplicate");
    }
    if (this.queue.length >= this.maxQueue) return this.result(false, "queue_full");

    this.recentKeys.set(key, now);
    this.activeKeys.add(key);
    this.queue.push(item);
    this.scheduleDrain();
    return this.result(true);
  }

  private result(accepted: boolean, reason?: EnqueueReason): EnqueueResult {
    return {
      accepted,
      reason,
      pending: this.queue.length,
      inFlight: this.inFlightCount,
    };
  }

  private pruneRecent(now: number): void {
    const cutoff = now - this.dedupeWindowMs;
    const stale: string[] = [];
    this.recentKeys.forEach((ts, key) => {
      if (ts < cutoff && !this.activeKeys.has(key)) stale.push(key);
    });
    for (let i = 0; i < stale.length; i++) this.recentKeys.delete(stale[i]);
  }

  private scheduleDrain(): void {
    if (this.drainScheduled) return;
    this.drainScheduled = true;
    this.scheduleFn(() => {
      this.drainScheduled = false;
      this.drain();
    }, 0);
  }

  private drain(): void {
    while (this.queue.length > 0 && this.inFlightCount < this.maxConcurrent) {
      const now = this.nowFn();
      const waited = now - this.lastTriggerAt;
      if (waited < this.minIntervalMs) {
        // Rate cap not yet satisfied; retry once the interval elapses.
        this.scheduleFn(() => this.drain(), this.minIntervalMs - waited);
        return;
      }

      const item = this.queue.shift() as GlitchyFeedback;
      const key = glitchyDedupeKey(item);
      this.lastTriggerAt = now;
      this.inFlightCount++;

      let running: Promise<void> | void;
      try {
        running = this.triggerFn(item);
      } catch (err) {
        this.log("[glitchy-worker] trigger threw: " + errText(err));
        this.onSettled(key);
        continue;
      }
      Promise.resolve(running)
        .catch((err) => this.log("[glitchy-worker] trigger rejected: " + errText(err)))
        .finally(() => this.onSettled(key));
    }
  }

  private onSettled(key: string): void {
    this.inFlightCount = Math.max(0, this.inFlightCount - 1);
    this.activeKeys.delete(key);
    this.scheduleDrain();
  }
}

function errText(err: unknown): string {
  if (err && typeof err === "object" && "message" in err) {
    return String((err as { message: unknown }).message);
  }
  return String(err);
}

function numFromEnv(name: string, fallback: number): number {
  const raw = (process.env[name] || "").trim();
  if (!raw) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

/**
 * Default trigger: spawns the Glitchy → Cursor Cloud Agent bridge as a
 * detached, fire-and-forget subprocess. The bridge itself pushes fixes
 * directly to the starting branch (main) with no PR/Draft.
 */
export function createGlitchySpawnTrigger(
  scriptPath: string,
  pythonBin: string,
): (item: GlitchyFeedback) => Promise<void> {
  return (item) =>
    new Promise<void>((resolve) => {
      const payload = {
        user_feedback: item.userFeedback,
        crop_box: item.cropBox ?? null,
        gs_logs: item.gsLogs ?? "",
        page_state: item.pageState ?? {},
      };
      try {
        const child = spawn(
          pythonBin,
          [scriptPath, "--cli", JSON.stringify(payload)],
          {
            cwd: process.cwd(),
            env: process.env,
            stdio: "ignore",
            detached: true,
            windowsHide: true,
          },
        );
        child.on("error", () => resolve());
        child.unref();
        resolve();
      } catch {
        resolve();
      }
    });
}

let singleton: GlitchyWorker | null = null;

/**
 * Lazily create the process-wide Glitchy worker. Enabled only when
 * GLITCHY_AUTORUN=1; all guards are tunable via GLITCHY_* env vars.
 */
export function getGlitchyWorker(
  scriptPath: string,
  pythonBin: string,
): GlitchyWorker {
  if (singleton) return singleton;
  singleton = new GlitchyWorker({
    enabled: process.env.GLITCHY_AUTORUN === "1",
    minIntervalMs: numFromEnv("GLITCHY_MIN_INTERVAL_MS", 30_000),
    maxConcurrent: numFromEnv("GLITCHY_MAX_CONCURRENT", 1),
    maxQueue: numFromEnv("GLITCHY_MAX_QUEUE", 50),
    dedupeWindowMs: numFromEnv("GLITCHY_DEDUPE_WINDOW_MS", 5 * 60_000),
    trigger: createGlitchySpawnTrigger(scriptPath, pythonBin),
    logger: (m) => console.log(m),
  });
  return singleton;
}
