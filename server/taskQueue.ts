import crypto from "crypto";

export type CompileState = "PENDING" | "PROCESSING" | "PACKAGING" | "COMPLETE" | "FAILURE";

export interface CompileAuditReport {
  geometry: { action_taken: string };
  typography: { action_taken: string };
  color_and_ink: { action_taken: string };
  resolution_and_lenses: { action_taken: string };
}

export interface CompileTask {
  taskId: string;
  jobId: number;
  state: CompileState;
  message: string;
  createdAt: number;
  updatedAt: number;
  downloadUrl?: string;
  outputPath?: string;
  error?: string;
  auditReport?: CompileAuditReport;
  glitchyMessage?: string;
  glitchyState?: string;
}

const tasks = new Map<string, CompileTask>();

const MAX_AGE_MS = 30 * 60 * 1000;

export function createTask(jobId: number): CompileTask {
  const taskId = crypto.randomBytes(12).toString("hex");
  const now = Date.now();
  const task: CompileTask = {
    taskId,
    jobId,
    state: "PENDING",
    message: "Waiting in queue...",
    createdAt: now,
    updatedAt: now,
  };
  tasks.set(taskId, task);
  return task;
}

export function getTask(taskId: string): CompileTask | undefined {
  return tasks.get(taskId);
}

export function updateTask(taskId: string, updates: Partial<CompileTask>): void {
  const task = tasks.get(taskId);
  if (!task) return;
  Object.assign(task, updates, { updatedAt: Date.now() });
}

export function cleanStaleTasks(): void {
  const cutoff = Date.now() - MAX_AGE_MS;
  for (const [id, task] of tasks) {
    if (task.updatedAt < cutoff) {
      tasks.delete(id);
    }
  }
}
