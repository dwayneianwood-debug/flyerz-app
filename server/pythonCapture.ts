import { spawn } from "child_process";
import { decodeChildText, pythonChildEnv } from "./pythonChildEnv";

const PYTHON_BIN = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");

export function parseCapturedJson(stdout: string): any {
  const trimmed = String(stdout || "").trim();
  if (!trimmed) throw new SyntaxError("empty");
  try {
    return JSON.parse(trimmed);
  } catch {
    const lines = trimmed.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      const line = lines[i];
      if (!line.startsWith("{") && !line.startsWith("[")) continue;
      try {
        return JSON.parse(line);
      } catch {
        /* keep looking for the JSON payload */
      }
    }
    throw new SyntaxError("invalid");
  }
}

/** Cat and edit Python. The event loop stays free while the child runs. */
export function execPythonAsync(args: string[], label: string, timeoutMs: number): Promise<any> {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, args, {
      cwd: process.cwd(),
      env: pythonChildEnv(),
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];
    let timedOut = false;
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      fn();
    };
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      stdoutChunks.push(Buffer.from(chunk));
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderrChunks.push(Buffer.from(chunk));
    });
    child.on("error", (error) => {
      finish(() => reject(new Error(`${label} failed to start Python (${PYTHON_BIN}): ${error.message}`)));
    });
    child.on("close", (status, signal) => {
      finish(() => {
        const stdout = decodeChildText(stdoutChunks);
        const stderr = decodeChildText(stderrChunks);
        if (timedOut) {
          reject(new Error(`${label} timed out after ${timeoutMs / 1000} seconds.`));
          return;
        }
        if (signal) {
          reject(new Error(`${label} terminated by signal ${signal}.${stderr ? ` ${stderr.slice(-500)}` : ""}`));
          return;
        }
        if (status !== 0) {
          reject(new Error(`${label} failed (exit ${status}). ${stderr ? stderr.slice(-500) : stdout.slice(-300) || "See server log."}`));
          return;
        }
        try {
          const parsed = parseCapturedJson(stdout);
          if (parsed && parsed.success === false) {
            reject(new Error(parsed.error || `${label} failed`));
            return;
          }
          resolve(parsed);
        } catch (err: any) {
          if (err instanceof SyntaxError) {
            reject(new Error(`${label} returned invalid JSON.`));
            return;
          }
          reject(err);
        }
      });
    });
  });
}
