import crypto from "crypto";
import fs from "fs";
import type { NextFunction, Request, Response } from "express";
import multer from "multer";
import { UPLOAD_LIMIT_BYTES, uploadTooLargeMessage } from "@shared/uploadLimit";

export { UPLOAD_LIMIT_BYTES, uploadTooLargeMessage };

function tooLargeBytes(req: Request): number {
  const length = Number(req.headers["content-length"]);
  if (Number.isFinite(length) && length > UPLOAD_LIMIT_BYTES) return length;
  return UPLOAD_LIMIT_BYTES + 1;
}

/** Stop a body that already declares it is over the limit, before it is written. */
export function rejectOversizedUpload(req: Request, res: Response, next: NextFunction): void {
  const length = Number(req.headers["content-length"]);
  if (Number.isFinite(length) && length > UPLOAD_LIMIT_BYTES) {
    res.status(413).type("text/plain; charset=utf-8").send(uploadTooLargeMessage(length));
    return;
  }
  next();
}

/** Multer's own cap. The message stays plain text and the status stays 413. */
export function uploadLimitError(err: unknown, req: Request, res: Response, next: NextFunction): void {
  const code = err && typeof err === "object" && "code" in err ? String((err as { code?: string }).code || "") : "";
  if (code === "LIMIT_FILE_SIZE") {
    if (!res.headersSent) {
      res.status(413).type("text/plain; charset=utf-8").send(uploadTooLargeMessage(tooLargeBytes(req)));
      return;
    }
  }
  next(err);
}

/** Stream the upload onto disk. The bytes are not held in memory. */
export function diskUpload(destDir: string, fileFilter?: multer.Options["fileFilter"]): multer.Multer {
  return multer({
    storage: multer.diskStorage({
      destination: (_req, _file, cb) => {
        fs.mkdirSync(destDir, { recursive: true });
        cb(null, destDir);
      },
      filename: (_req, _file, cb) => {
        cb(null, crypto.randomBytes(16).toString("hex"));
      },
    }),
    limits: { fileSize: UPLOAD_LIMIT_BYTES },
    fileFilter,
  });
}
