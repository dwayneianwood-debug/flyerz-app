import type { Express, Request, Response } from "express";
import multer from "multer";
import path from "path";
import fs from "fs/promises";
import fsSync from "fs";
import { spawn } from "child_process";
import crypto from "crypto";

const PYTHON_BIN = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");
const PURE_CROP_SCRIPT = path.join(process.cwd(), "server", "pure_crop.py");
const pureCropDir = path.join(process.cwd(), "uploads", "pure-crop");
fsSync.mkdirSync(pureCropDir, { recursive: true });

const pureCropUpload = multer({
  dest: pureCropDir,
  limits: { fileSize: 50 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    if (ext === ".pdf" || ext === ".jpg" || ext === ".jpeg" || ext === ".png") {
      cb(null, true);
    } else {
      cb(new Error("Only JPG, PNG, and PDF files can be cropped."));
    }
  },
});

type PythonResult = { code: number | null; stdout: string; stderr: string };

function runPython(args: string[]): Promise<PythonResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, args, { cwd: process.cwd() });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

function parsePythonJson(result: PythonResult): any {
  const line = result.stdout.trim().split("\n").filter(Boolean).pop() || "";
  try {
    return JSON.parse(line);
  } catch {
    throw new Error(result.stderr.trim() || "Crop tool returned an unreadable result");
  }
}

function safeBasename(name: string): string {
  const base = path.basename(name).replace(/[\\/]/g, "");
  if (!base || base.includes("..")) return "";
  return base;
}

function fileTypeFromName(name: string): "jpg" | "png" | "pdf" | null {
  const ext = path.extname(name).toLowerCase();
  if (ext === ".jpg" || ext === ".jpeg") return "jpg";
  if (ext === ".png") return "png";
  if (ext === ".pdf") return "pdf";
  return null;
}

function downloadName(originalName: string, ext: string): string {
  const stem = path.basename(originalName, path.extname(originalName)).replace(/[\\/]/g, "") || "artwork";
  return `${stem}_cropped.${ext}`;
}

async function storedPath(filename: string): Promise<string> {
  const base = safeBasename(filename);
  if (!base) {
    throw new Error("Invalid stored file");
  }
  const full = path.resolve(pureCropDir, base);
  if (!full.startsWith(path.resolve(pureCropDir) + path.sep)) {
    throw new Error("Invalid stored file");
  }
  await fs.access(full);
  return full;
}

export function registerPureCropRoutes(app: Express) {
  app.post("/api/pure-crop/inspect", pureCropUpload.single("file"), async (req: Request, res: Response) => {
    try {
      if (!req.file) {
        return res.status(400).json({ message: "No file uploaded" });
      }
      const fileType = fileTypeFromName(req.file.originalname);
      if (!fileType) {
        await fs.unlink(req.file.path).catch(() => undefined);
        return res.status(400).json({ message: "Only JPG, PNG, and PDF files can be cropped." });
      }
      const storedFilename = `${crypto.randomBytes(8).toString("hex")}_${safeBasename(req.file.originalname)}`;
      const finalPath = path.join(pureCropDir, storedFilename);
      await fs.mkdir(pureCropDir, { recursive: true });
      await fs.rename(req.file.path, finalPath);

      const inspected = parsePythonJson(await runPython([PURE_CROP_SCRIPT, "inspect", finalPath]));
      if (!inspected.success) {
        return res.status(400).json({ message: inspected.error || "Could not read that file" });
      }

      let previewFilename: string | null = null;
      if (fileType === "pdf") {
        previewFilename = `${crypto.randomBytes(6).toString("hex")}_p1.png`;
        const previewPath = path.join(pureCropDir, previewFilename);
        const preview = parsePythonJson(
          await runPython([PURE_CROP_SCRIPT, "preview", finalPath, previewPath, "1"]),
        );
        if (!preview.success) {
          return res.status(400).json({ message: preview.error || "Could not preview that PDF" });
        }
        inspected.preview = preview;
        inspected.previewUrl = `/api/pure-crop/preview/${previewFilename}`;
      }

      res.json({
        ...inspected,
        originalFilename: req.file.originalname,
        storedFilename,
        fileType,
      });
    } catch (error) {
      console.error("[PURE-CROP] inspect failed", error);
      res.status(500).json({ message: error instanceof Error ? error.message : "Could not open that file" });
    }
  });

  app.post("/api/pure-crop/preview-page", async (req: Request, res: Response) => {
    try {
      const storedFilename = String(req.body?.storedFilename || "");
      const page = Number(req.body?.page || 1);
      const full = await storedPath(storedFilename);
      const previewFilename = `${crypto.randomBytes(6).toString("hex")}_p${page}.png`;
      const previewPath = path.join(pureCropDir, previewFilename);
      const preview = parsePythonJson(
        await runPython([PURE_CROP_SCRIPT, "preview", full, previewPath, String(page)]),
      );
      if (!preview.success) {
        return res.status(400).json({ message: preview.error || "Could not preview that page" });
      }
      res.json({ ...preview, previewUrl: `/api/pure-crop/preview/${previewFilename}` });
    } catch (error) {
      res.status(400).json({ message: error instanceof Error ? error.message : "Could not preview that page" });
    }
  });

  app.get("/api/pure-crop/preview/:filename", async (req: Request, res: Response) => {
    try {
      const full = await storedPath(String(req.params.filename));
      res.setHeader("Content-Type", "image/png");
      res.setHeader("Cache-Control", "no-store");
      res.sendFile(full);
    } catch {
      res.status(404).json({ message: "Preview not found" });
    }
  });

  app.post("/api/pure-crop/save", async (req: Request, res: Response) => {
    try {
      const storedFilename = String(req.body?.storedFilename || "");
      const originalFilename = String(req.body?.originalFilename || "artwork");
      const output = String(req.body?.output || "");
      const shape = req.body?.shape === "ellipse" ? "ellipse" : "rect";
      const fileType = fileTypeFromName(originalFilename) || (req.body?.fileType as string);
      if (fileType !== "jpg" && fileType !== "png" && fileType !== "pdf") {
        return res.status(400).json({ message: "Only JPG, PNG, and PDF files can be cropped." });
      }
      if (output !== "original" && output !== "pdf" && output !== "jpg") {
        return res.status(400).json({ message: "Choose original, PDF, or JPG output." });
      }
      const x = Number(req.body?.x);
      const y = Number(req.body?.y);
      const width = Number(req.body?.width);
      const height = Number(req.body?.height);
      if (![x, y, width, height].every((n) => Number.isFinite(n)) || width <= 0 || height <= 0) {
        return res.status(400).json({ message: "Crop width and height must be greater than zero." });
      }
      const full = await storedPath(storedFilename);
      const ext = output === "original" ? (fileType === "jpg" ? "jpg" : fileType) : output === "pdf" ? "pdf" : "jpg";
      const outFilename = `${crypto.randomBytes(6).toString("hex")}_cropped.${ext}`;
      const outPath = path.join(pureCropDir, outFilename);
      const options = {
        fileType,
        output,
        shape,
        page: Number(req.body?.page || 1),
        x,
        y,
        width,
        height,
        jpgDpi: Number(req.body?.jpgDpi || 300),
      };
      const saved = parsePythonJson(
        await runPython([PURE_CROP_SCRIPT, "save", full, outPath, JSON.stringify(options)]),
      );
      if (!saved.success) {
        return res.status(400).json({ message: saved.error || "Crop failed" });
      }
      const filename = downloadName(originalFilename, ext);
      const contentType = ext === "pdf" ? "application/pdf" : ext === "png" ? "image/png" : "image/jpeg";
      res.setHeader("Content-Type", contentType);
      res.setHeader(
        "Content-Disposition",
        `attachment; filename="${filename.replace(/"/g, "")}"; filename*=UTF-8''${encodeURIComponent(filename)}`,
      );
      res.setHeader("Cache-Control", "no-store");
      res.sendFile(outPath, (err) => {
        if (err) console.error("[PURE-CROP] send failed", err);
      });
    } catch (error) {
      console.error("[PURE-CROP] save failed", error);
      res.status(500).json({ message: error instanceof Error ? error.message : "Crop failed" });
    }
  });
}
