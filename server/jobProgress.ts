import fs from "fs";
import path from "path";
import { AsyncLocalStorage } from "async_hooks";
import type { JobProgress } from "@shared/jobProgress";

const storage = new AsyncLocalStorage<string>();

const STAGES: Record<string, { percent: number; label: string }> = {
  checking: { percent: 8, label: "Checking the file" },
  fitting: { percent: 12, label: "Fitting the picture" },
  reading: { percent: 28, label: "Reading the words" },
  removing: { percent: 46, label: "Removing the old words" },
  enlarging: { percent: 64, label: "Enlarging the artwork" },
  press: { percent: 78, label: "Building the press PDF" },
  proof: { percent: 90, label: "Making the proof" },
  done: { percent: 100, label: "Finished" },
};

const REBUILD_NOTE = "AI rebuild can take 2–3 minutes.";

export function jobProgressPath(jobId: number): string {
  return path.join(process.cwd(), "uploads", "job-progress", `${jobId}.json`);
}

export function progressFileFromContext(): string | undefined {
  return storage.getStore();
}

export function runWithJobProgress<T>(jobId: number, work: () => T): T {
  return storage.run(jobProgressPath(jobId), work);
}

export function readJobProgress(jobId: number): JobProgress | null {
  const file = jobProgressPath(jobId);
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8")) as JobProgress;
    if (!raw || typeof raw.stage !== "string") return null;
    const started = Number(raw.startedAt) || Date.now();
    return {
      stage: raw.stage,
      stageId: String(raw.stageId || ""),
      percent: Number(raw.percent) || 0,
      startedAt: started,
      updatedAt: Number(raw.updatedAt) || started,
      note: String(raw.note || ""),
      elapsedSec: Math.max(0, Math.round((Date.now() - started) / 1000)),
    };
  } catch {
    return null;
  }
}

export function writeJobProgress(jobId: number, stage: string, note = ""): void {
  const known = STAGES[stage] || { percent: 8, label: stage };
  const file = jobProgressPath(jobId);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  let previous: Partial<JobProgress> = {};
  try {
    previous = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    previous = {};
  }
  let percent = known.percent;
  if (stage !== "done" && Number(previous.percent) > percent) percent = Number(previous.percent);
  let kept = note.trim();
  if (!kept && (stage === "reading" || stage === "removing" || stage === "enlarging")) kept = REBUILD_NOTE;
  if (!kept) kept = String(previous.note || "");
  const payload: JobProgress = {
    stage: known.label,
    stageId: stage,
    percent,
    startedAt: Number(previous.startedAt) || Date.now(),
    updatedAt: Date.now(),
    note: kept,
  };
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(payload));
  fs.renameSync(temporary, file);
}
