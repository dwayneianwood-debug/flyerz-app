/** How often to refresh a job. Idle and finished jobs are not polled. */
export function jobPollIntervalMs(status: string | undefined, updateCount: number): number | false {
  if (status !== "pending" && status !== "processing" && status !== "queued") return false;
  if (updateCount >= 12) return 10000;
  if (updateCount >= 4) return 5000;
  return 2000;
}

/** Batch upload status checks. Slows down while files are still working. */
export function batchPollIntervalMs(tick: number): number {
  if (tick >= 8) return 12000;
  if (tick >= 4) return 6000;
  return 3000;
}

/** Pre-compile watcher. Stops once the work is finished. Idle "none" checks stop after 8, which is when the page treats the file as ready. */
export function precompilePollDelayMs(state: string, polls: number): number | false {
  if (state === "ready" || state === "failed" || state === "error") return false;
  if (state === "none" && polls >= 8) return false;
  if (state === "compiling" && polls > 8) return 5000;
  return 2000;
}
