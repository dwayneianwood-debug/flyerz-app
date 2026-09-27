import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import express from "express";
import zlib from "node:zlib";
import { compressResponses, preferredEncoding } from "./httpCompression.ts";
import { cacheControlForStaticFile } from "./staticCache.ts";
import { batchPollIntervalMs, jobPollIntervalMs, precompilePollDelayMs } from "../client/src/lib/poll-backoff.ts";

test("job list page omits audit JSON and keeps the sort indexes", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-jobs-"));
  process.env.FLYERZ_DB_PATH = path.join(dir, "jobs.sqlite");
  const { storage } = await import("./storage.ts");
  for (let i = 0; i < 3; i++) {
    const job = await storage.createJob({
      filename: `card-${i}.jpg`,
      originalPath: `/tmp/card-${i}.jpg`,
      fileSize: 1000 + i,
      fileType: "jpg",
    });
    await storage.updateJob(job.id, {
      status: "complete",
      correctedPath: i === 2 ? `/tmp/card-${i}-press.pdf` : undefined,
      auditResults: {
        checks: [],
        overallPassed: i === 2,
        fixesApplied: 0,
        complianceReport: `SECRET-AUDIT-BLOB-${i}`,
        pressEngine: i === 2 ? { passed: true, status: "ready", headline: "Ready for press" } : { passed: false, status: "planned" },
      },
    });
  }

  const page = await storage.listJobs({ limit: 2, offset: 0 });
  assert.equal(page.jobs.length, 2);
  assert.equal(page.total, 3);
  assert.equal(page.hasMore, true);
  assert.equal(page.jobs[0].filename, "card-2.jpg");
  assert.equal(page.jobs[0].printReady, true);
  assert.equal(page.jobs[0].hasCorrectedFile, true);
  assert.equal(page.jobs[1].printReady, false);
  const body = JSON.stringify(page);
  assert.equal(body.includes("SECRET-AUDIT-BLOB"), false);
  assert.equal(body.includes("auditResults"), false);
  assert.equal(body.includes("originalPath"), false);

  const names = storage.listIndexNames();
  assert.ok(names.includes("idx_file_jobs_uploaded_at"));
  assert.ok(names.includes("idx_file_jobs_status_uploaded_at"));

  const status = await storage.getJobStatus(page.jobs[0].id);
  assert.equal(status?.status, "complete");
  assert.equal(JSON.stringify(status).includes("SECRET-AUDIT-BLOB"), false);
});

test("a large script is brotli-compressed and the response finishes", async () => {
  const app = express();
  app.use(compressResponses);
  const payload = "console.log('flyerz');\n".repeat(20_000);
  app.get("/app.js", (_req, res) => {
    res.type("application/javascript").send(payload);
  });
  app.get("/piped.js", (_req, res) => {
    res.type("application/javascript");
    res.setHeader("Content-Length", Buffer.byteLength(payload));
    const slice = 16 * 1024;
    Readable.from((function* () {
      for (let i = 0; i < payload.length; i += slice) yield payload.slice(i, i + slice);
    })()).pipe(res);
  });
  const server = http.createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  try {
    const raw = await new Promise<{ status: number; encoding: string; body: Buffer }>((resolve, reject) => {
      const req = http.request(
        { hostname: "127.0.0.1", port, path: "/app.js", headers: { "Accept-Encoding": "br" } },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
          res.on("end", () => resolve({
            status: res.statusCode || 0,
            encoding: String(res.headers["content-encoding"] || ""),
            body: Buffer.concat(chunks),
          }));
        },
      );
      req.setTimeout(3000, () => {
        req.destroy();
        reject(new Error("compression response hung"));
      });
      req.on("error", reject);
      req.end();
    });
    assert.equal(raw.status, 200);
    assert.equal(raw.encoding, "br");
    const text = zlib.brotliDecompressSync(raw.body).toString("utf8");
    assert.equal(text, payload);
    assert.ok(raw.body.length < payload.length);

    const piped = await new Promise<{ status: number; encoding: string; body: Buffer }>((resolve, reject) => {
      const req = http.request(
        { hostname: "127.0.0.1", port, path: "/piped.js", headers: { "Accept-Encoding": "br" } },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
          res.on("end", () => resolve({
            status: res.statusCode || 0,
            encoding: String(res.headers["content-encoding"] || ""),
            body: Buffer.concat(chunks),
          }));
        },
      );
      req.setTimeout(3000, () => {
        req.destroy();
        reject(new Error("piped compression response hung"));
      });
      req.on("error", reject);
      req.end();
    });
    assert.equal(piped.status, 200);
    assert.equal(piped.encoding, "br");
    assert.equal(zlib.brotliDecompressSync(piped.body).toString("utf8"), payload);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("static files and compression pick long cache and brotli", () => {
  assert.equal(cacheControlForStaticFile("C:\\app\\dist\\public\\index.html"), "no-cache");
  assert.equal(
    cacheControlForStaticFile("/app/dist/public/assets/index-abc123.js"),
    "public, max-age=31536000, immutable",
  );
  assert.equal(cacheControlForStaticFile("/app/dist/public/favicon.png"), "public, max-age=3600");
  assert.equal(preferredEncoding("gzip, deflate, br"), "br");
  assert.equal(preferredEncoding("gzip"), "gzip");
  assert.equal(preferredEncoding(""), null);
});

test("polling slows down and stops when nothing is in progress", () => {
  assert.equal(jobPollIntervalMs("processing", 1), 2000);
  assert.equal(jobPollIntervalMs("processing", 5), 5000);
  assert.equal(jobPollIntervalMs("complete", 1), false);
  assert.equal(batchPollIntervalMs(0), 3000);
  assert.equal(batchPollIntervalMs(9), 12000);
  assert.equal(precompilePollDelayMs("ready", 1), false);
  assert.equal(precompilePollDelayMs("none", 3), 2000);
  assert.equal(precompilePollDelayMs("none", 8), false);
  assert.equal(precompilePollDelayMs("compiling", 2), 2000);
  assert.equal(precompilePollDelayMs("compiling", 9), 5000);
});

test("production starter builds on demand and turns LAN mode on", () => {
  const prod = fs.readFileSync("start_prod.ps1", "utf8");
  const dev = fs.readFileSync("start_dev.ps1", "utf8");
  assert.match(prod, /LAN_ONLY_MODE = 'true'/);
  assert.match(prod, /npm run build/);
  assert.match(prod, /npm start/);
  assert.match(prod, /\[switch\] \$Build/);
  assert.match(prod, /Import-DotEnv/);
  assert.match(dev, /npm run dev/);
  assert.doesNotMatch(dev, /npm start/);
});
