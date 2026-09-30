/** One run per job. A second start joins the run that is already going. */

export interface JobRunHold {
  owner: string;
  startedAt: number;
}

const holds = new Map<number, JobRunHold>();

export function currentJobRun(jobId: number): JobRunHold | null {
  return holds.get(jobId) || null;
}

export function beginJobRun(jobId: number, owner: string): { started: true } | { started: false; current: JobRunHold } {
  const current = holds.get(jobId);
  if (current) return { started: false, current };
  holds.set(jobId, { owner, startedAt: Date.now() });
  return { started: true };
}

export function endJobRun(jobId: number, owner: string): void {
  const current = holds.get(jobId);
  if (current && current.owner === owner) holds.delete(jobId);
}

export class JobAlreadyRunning extends Error {
  hold: JobRunHold;

  constructor(hold: JobRunHold) {
    super("This job is already being processed.");
    this.name = "JobAlreadyRunning";
    this.hold = hold;
  }
}

/** True when a new POST must not start work. */
export function duplicateRun(status: string | undefined, jobId: number): boolean {
  return status === "processing" || currentJobRun(jobId) != null;
}
