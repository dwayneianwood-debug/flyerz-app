import type { NextFunction, Request, Response } from "express";
import zlib from "zlib";

const COMPRESSIBLE = /^(text\/|application\/(javascript|json|xml|xhtml\+xml)|image\/svg\+xml)/i;
const MIN_BYTES = 1024;

export function preferredEncoding(accept: string | undefined): "br" | "gzip" | null {
  const header = accept || "";
  if (/\bbr\b/.test(header)) return "br";
  if (/\bgzip\b/.test(header)) return "gzip";
  return null;
}

function asBuffer(chunk: unknown, encoding?: unknown): Buffer {
  if (chunk == null || chunk === "") return Buffer.alloc(0);
  if (Buffer.isBuffer(chunk)) return chunk;
  if (typeof chunk === "string") return Buffer.from(chunk, typeof encoding === "string" ? encoding as BufferEncoding : "utf8");
  if (chunk instanceof Uint8Array) return Buffer.from(chunk);
  return Buffer.from(String(chunk));
}

function endArgs(chunk: unknown, enc: unknown, cb: unknown): { payload: unknown; callback?: () => void } {
  if (typeof chunk === "function") return { payload: undefined, callback: chunk as () => void };
  const callback = typeof enc === "function" ? enc as () => void : typeof cb === "function" ? cb as () => void : undefined;
  return { payload: chunk, callback };
}

/**
 * Gzip or Brotli for text, JavaScript, and JSON.
 * The body is collected and compressed once. Streaming into zlib while
 * express.static pipes the file deadlocks on large hashed assets.
 * Downloads and pictures are left alone.
 */
export function compressResponses(req: Request, res: Response, next: NextFunction) {
  const encoding = preferredEncoding(
    Array.isArray(req.headers["accept-encoding"])
      ? req.headers["accept-encoding"].join(",")
      : req.headers["accept-encoding"],
  );
  if (!encoding || req.method === "HEAD" || req.headers.range) return next();

  const origWrite = res.write.bind(res);
  const origEnd = res.end.bind(res);
  const chunks: Buffer[] = [];
  let passthrough = false;
  let decided = false;

  const wantsCompression = () => {
    const type = String(res.getHeader("Content-Type") || "").split(";")[0].trim();
    const disposition = String(res.getHeader("Content-Disposition") || "");
    if (disposition.toLowerCase().includes("attachment") || !COMPRESSIBLE.test(type)) return false;
    const len = Number(res.getHeader("Content-Length") || 0);
    if (len > 0 && len < MIN_BYTES) return false;
    return true;
  };

  const applyEncoding = (body: Buffer) => {
    const compressed = encoding === "br"
      ? zlib.brotliCompressSync(body, { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 4 } })
      : zlib.gzipSync(body, { level: zlib.constants.Z_BEST_SPEED });
    res.removeHeader("Content-Length");
    res.setHeader("Content-Encoding", encoding);
    const vary = String(res.getHeader("Vary") || "");
    if (!vary.toLowerCase().split(",").map((part) => part.trim()).includes("Accept-Encoding")) {
      res.setHeader("Vary", vary ? `${vary}, Accept-Encoding` : "Accept-Encoding");
    }
    return compressed;
  };

  res.write = ((chunk: any, enc?: any, cb?: any) => {
    // A file stream may call writeHead before the body. Do not buffer that body
    // and then try to strip Content-Length after the headers have gone out.
    if (passthrough || res.headersSent) {
      passthrough = true;
      decided = true;
      return origWrite(chunk, enc, cb);
    }
    if (!decided) {
      decided = true;
      if (!wantsCompression()) {
        passthrough = true;
        return origWrite(chunk, enc, cb);
      }
    }
    const callback = typeof enc === "function" ? enc : cb;
    const piece = asBuffer(chunk, enc);
    if (piece.length) chunks.push(piece);
    if (typeof callback === "function") callback();
    return true;
  }) as typeof res.write;

  res.end = ((chunk?: any, enc?: any, cb?: any) => {
    const { payload, callback } = endArgs(chunk, enc, cb);
    if (passthrough || res.headersSent) return origEnd(chunk, enc, cb);
    if (!decided) {
      decided = true;
      if (!wantsCompression()) return origEnd(chunk, enc, cb);
    }
    if (payload != null && payload !== "") {
      const piece = asBuffer(payload, enc);
      if (piece.length) chunks.push(piece);
    }
    const body = chunks.length ? Buffer.concat(chunks) : Buffer.alloc(0);
    if (body.length < MIN_BYTES) return origEnd(body, callback);
    return origEnd(applyEncoding(body), callback);
  }) as typeof res.end;

  next();
}
