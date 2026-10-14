/** Live stage for a job. Stored beside the job, not in the database. */

export interface FixProgress {
  id: string;
  label: string;
  percent: number;
}

export interface JobProgress {
  stage: string;
  stageId: string;
  percent: number;
  startedAt: number;
  updatedAt: number;
  note: string;
  elapsedSec?: number;
  fixes?: FixProgress[];
}

const FIX_ORDER = ["checking", "fitting", "reading", "removing", "enlarging", "press", "proof"] as const;

const FIX_LABELS: Record<string, { percent: number; label: string }> = {
  checking: { percent: 8, label: "Checking the file" },
  fitting: { percent: 12, label: "Fitting the picture" },
  reading: { percent: 28, label: "Reading the words" },
  removing: { percent: 46, label: "Removing the old words" },
  enlarging: { percent: 64, label: "Enlarging the artwork" },
  press: { percent: 78, label: "Building the press PDF" },
  proof: { percent: 90, label: "Making the proof" },
};

export function fixRows(stageId: string): FixProgress[] {
  const current = stageId === "done" ? "done" : (stageId in FIX_LABELS ? stageId : "checking");
  const currentIndex = current === "done" ? FIX_ORDER.length : FIX_ORDER.indexOf(current as typeof FIX_ORDER[number]);
  return FIX_ORDER.map((key, index) => {
    const known = FIX_LABELS[key];
    const percent = index < currentIndex ? 100 : index === currentIndex ? known.percent : 0;
    return { id: key, label: known.label, percent };
  });
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
