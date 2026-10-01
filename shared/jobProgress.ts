/** Live stage for a job. Stored beside the job, not in the database. */

export interface JobProgress {
  stage: string;
  stageId: string;
  percent: number;
  startedAt: number;
  updatedAt: number;
  note: string;
  elapsedSec?: number;
}

/** In-progress percents stop at 90 so the bar never sits on a fake 95. */
export function displayPercent(status: string | undefined, percent?: number | null): number {
  if (status === "complete" || status === "failed") return 100;
  const value = percent == null || Number.isNaN(Number(percent)) ? 8 : Number(percent);
  if (status === "processing" || status === "pending" || status === "queued") {
    return Math.min(90, Math.max(0, Math.round(value)));
  }
  return Math.min(100, Math.max(0, Math.round(value)));
}

export function formatElapsed(seconds: number | null | undefined): string {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes <= 0) return `${rest}s`;
  return `${minutes}m ${rest}s`;
}
