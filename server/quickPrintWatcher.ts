import fs from "fs";
import path from "path";
import { storage } from "./storage";
import { readQuickPrintSettings, type QuickPrintSettings } from "./quickPrintSettings";
import { fileTypeForName, jobOutputDir, redFallback, runQuickPrintFile, saveQuickResult } from "./quickPrintRunner";

const SKIP = new Set(["thumbs.db", "desktop.ini", ".ds_store"]);
const seen = new Map<string, { size: number; mtimeMs: number }>();
let timer: NodeJS.Timeout | null = null;
let scanning = false;

function safeStem(filename: string): string {
  const base = path.basename(filename, path.extname(filename));
  const cleaned = base.replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "");
  return cleaned.slice(0, 80) || "artwork";
}

function copyIfPresent(from: string, to: string) {
  if (fs.existsSync(from)) fs.copyFileSync(from, to);
}

export async function processDropFile(folderPath: string, filePath: string, settings: QuickPrintSettings): Promise<string> {
  const filename = path.basename(filePath);
  const job = await storage.createJob({
    filename,
    originalPath: filePath,
    fileSize: fs.statSync(filePath).size,
    fileType: fileTypeForName(filename),
  });
  const outputDir = jobOutputDir(job.id);
  let result;
  try {
    result = await runQuickPrintFile(filePath, outputDir, {
      productId: settings.defaultProductId,
      filename,
      detectSize: true,
      notes: "",
      quantity: null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "The file could not be finished.";
    result = redFallback(message);
  }
  await saveQuickResult(job.id, result);
  const bucket = result.light === "green" ? "Output" : "Needs-attention";
  const dest = path.join(folderPath, bucket, `${safeStem(filename)}-${job.id}`);
  fs.mkdirSync(dest, { recursive: true });
  copyIfPresent(path.join(outputDir, "press.pdf"), path.join(dest, "press.pdf"));
  copyIfPresent(path.join(outputDir, "proof.png"), path.join(dest, "proof.png"));
  copyIfPresent(path.join(outputDir, "proof.pdf"), path.join(dest, "proof.pdf"));
  copyIfPresent(path.join(outputDir, "decisions.txt"), path.join(dest, "decisions.txt"));
  const kept = path.join(dest, filename);
  try {
    fs.renameSync(filePath, kept);
  } catch {
    fs.copyFileSync(filePath, kept);
    try { fs.unlinkSync(filePath); } catch { /* the copy is already in the result folder */ }
  }
  return dest;
}

export async function scanDropFolderOnce(options?: { force?: boolean }): Promise<{ processed: string[] }> {
  const settings = readQuickPrintSettings();
  if (!settings.enabled || !settings.folderPath || !path.isAbsolute(settings.folderPath)) {
    return { processed: [] };
  }
  const root = settings.folderPath;
  const input = path.join(root, "Input");
  fs.mkdirSync(input, { recursive: true });
  fs.mkdirSync(path.join(root, "Output"), { recursive: true });
  fs.mkdirSync(path.join(root, "Needs-attention"), { recursive: true });
  let names: string[] = [];
  try {
    names = fs.readdirSync(input);
  } catch {
    return { processed: [] };
  }
  const processed: string[] = [];
  for (const name of names) {
    if (name.startsWith(".") || SKIP.has(name.toLowerCase())) continue;
    const full = path.join(input, name);
    let stat: fs.Stats;
    try {
      stat = fs.statSync(full);
    } catch {
      continue;
    }
    if (!stat.isFile() || stat.size <= 0) continue;
    const previous = seen.get(full);
    const stable = !!previous && previous.size === stat.size && previous.mtimeMs === stat.mtimeMs;
    seen.set(full, { size: stat.size, mtimeMs: stat.mtimeMs });
    if (!options?.force && !stable) continue;
    seen.delete(full);
    processed.push(await processDropFile(root, full, settings));
  }
  return { processed };
}

export function startQuickPrintWatcher() {
  if (timer) return;
  timer = setInterval(() => {
    if (scanning) return;
    scanning = true;
    scanDropFolderOnce()
      .catch((error) => {
        console.error("[QUICK-PRINT] drop folder:", error instanceof Error ? error.message : error);
      })
      .finally(() => {
        scanning = false;
      });
  }, 4000);
  timer.unref?.();
}
