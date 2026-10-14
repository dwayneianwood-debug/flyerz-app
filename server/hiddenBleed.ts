/**
 * Hidden bleed recovery (see server/hidden_bleed.py).
 * Off unless FLYERZ_HIDDEN_BLEED=1. With the flag off every helper here is a no-op,
 * so production behaviour is unchanged.
 */
import { spawnSync } from "child_process";
import path from "path";
import { pythonChildEnv } from "./pythonChildEnv";

export type HiddenBleedChoice = "use" | "ignore";

const PYTHON_BIN = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");
const SCRIPT = path.join(process.cwd(), "server", "hidden_bleed.py");

export function hiddenBleedEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return /^(1|true|yes|on)$/i.test(String(env.FLYERZ_HIDDEN_BLEED || "").trim());
}

/** Only 'use' / 'ignore' survive; anything else is dropped (default = use). */
export function sanitizeHiddenBleedChoice(value: unknown): HiddenBleedChoice | undefined {
  return value === "use" || value === "ignore" ? value : undefined;
}

export function shouldTryHiddenBleed(
  options: { hiddenBleed?: unknown } | undefined,
  pipelineType: string,
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  if (!hiddenBleedEnabled(env)) return false;
  if (String(pipelineType).toLowerCase() !== "pdf") return false;
  return sanitizeHiddenBleedChoice(options?.hiddenBleed) !== "ignore";
}

export interface HiddenBleedRecovery {
  recovered: boolean;
  output?: string;
  reason?: string;
  trimMm?: [number, number];
}

/**
 * First pipeline step: grow the page boxes onto artwork that already runs past the edge.
 * Never throws; on any failure returns { recovered: false } and the caller keeps its input.
 */
export function recoverHiddenBleed(inputPdf: string, outputPdf: string, bleedMm = 5): HiddenBleedRecovery {
  try {
    const proc = spawnSync(PYTHON_BIN, [SCRIPT, "recover", inputPdf, outputPdf, "--bleed-mm", String(bleedMm)], {
      cwd: process.cwd(),
      env: pythonChildEnv(),
      encoding: "utf8",
      timeout: 60_000,
      maxBuffer: 10 * 1024 * 1024,
    });
    if (proc.error || proc.status !== 0) return { recovered: false, reason: String(proc.error || proc.stderr || "").slice(-300) };
    const line = String(proc.stdout || "").trim().split(/\r?\n/).pop() || "{}";
    const parsed = JSON.parse(line);
    return {
      recovered: parsed.recovered === true,
      output: parsed.recovered ? outputPdf : undefined,
      reason: parsed.reason,
      trimMm: Array.isArray(parsed.trimMm) ? parsed.trimMm : undefined,
    };
  } catch (error: any) {
    return { recovered: false, reason: String(error?.message || error) };
  }
}

/** A pre-resize to a different size would throw the recovered boxes away, so only keep matching trims. */
export function recoveredTrimMatches(trimMm: [number, number] | undefined, targetW?: number | null, targetH?: number | null): boolean {
  if (!targetW || !targetH) return true;
  if (!trimMm) return false;
  const [w, h] = trimMm;
  const close = (a: number, b: number) => Math.abs(a - b) <= 2;
  return (close(w, targetW) && close(h, targetH)) || (close(w, targetH) && close(h, targetW));
}
