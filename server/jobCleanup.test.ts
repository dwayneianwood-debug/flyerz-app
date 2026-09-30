import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import express from "express";
import { DatabaseSync } from "node:sqlite";
import {
  backupDatabaseFile,
  msUntilJustAfterMidnight,
  planCleanup,
  readJobCleanupSettings,
  registerJobCleanupRoutes,
  runJobCleanup,
  startOfTodayJohannesburg,
  writeJobCleanupSettings,
} from "./jobCleanup.ts";

const NOW = new Date("2026-09-30T06:00:00.000Z");

function appDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "flyerz-cleanup-"));
}

function write(file: string, body: string): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, body);
}

test("yesterday is removed, today and a processing job stay, and outside paths are never touched", async () => {
  const root = appDir();
  const uploads = path.join(root, "uploads");
  const jobsRoot = path.join(root, "jobs");
  const data = path.join(root, "data");
  const outside = path.join(root, "outside");
  const backupDir = path.join(data, "backups");
  const databasePath = path.join(data, "flyerz.sqlite");
  write(databasePath, "");
  const sqlite = new DatabaseSync(databasePath);
  sqlite.exec("CREATE TABLE file_jobs (id INTEGER PRIMARY KEY, filename TEXT)");
  sqlite.prepare("INSERT INTO file_jobs (filename) VALUES (?)").run("kept-in-backup");

  write(path.join(uploads, "old.png"), "old-artwork");
  write(path.join(uploads, "quick-print", "1", "press.pdf"), "press");
  write(path.join(uploads, "quick-print", "1", "proof.png"), "proof");
  write(path.join(uploads, "thumbs", "1.jpg"), "thumb");
  write(path.join(uploads, "1_press_ready_precompile.pdf"), "precompile");
  write(path.join(uploads, "12_keep.pdf"), "other-job");
  write(path.join(jobsRoot, "old.txt"), "job-folder");
  write(path.join(uploads, "today.png"), "today-artwork");
  write(path.join(uploads, "busy.png"), "still-printing");
  write(path.join(outside, "secret.pdf"), "do-not-touch");
  write(path.join(outside, "nested", "note.txt"), "do-not-touch-dir");
  write(path.join(root, "uploads-evil", "file.pdf"), "sibling");
  const backupAlready = path.join(backupDir, "manual-copy.sqlite");
  write(backupAlready, "backup-copy");
  fs.symlinkSync(path.join(outside, "secret.pdf"), path.join(uploads, "link-out.pdf"));

  const removed: number[] = [];
  const logs: string[] = [];
  let backups = 0;
  const report = await runJobCleanup({
    now: NOW,
    reason: "startup",
    enabled: true,
    jobs: [
      {
        id: 1,
        filename: "old.png",
        status: "complete",
        uploadedAt: "2026-09-29 18:00:00",
        originalPath: path.join(uploads, "old.png"),
        correctedPath: path.join(uploads, "quick-print", "1", "press.pdf"),
        auditResults: {
          proofPath: path.join(uploads, "quick-print", "1", "proof.png"),
          healthReportPath: path.join(outside, "secret.pdf"),
          compiledPdfPath: path.join(outside, "nested"),
          quickPrint: { pressPath: path.join(root, "uploads-evil", "file.pdf") },
          note: path.join(backupAlready),
          database: databasePath,
          jobFolder: path.join(jobsRoot, "old.txt"),
          linked: path.join(uploads, "link-out.pdf"),
        },
      },
      {
        id: 2,
        filename: "today.png",
        status: "complete",
        uploadedAt: "2026-09-29 23:00:00",
        originalPath: path.join(uploads, "today.png"),
      },
      {
        id: 3,
        filename: "busy.png",
        status: "processing",
        uploadedAt: "2026-09-29 12:00:00",
        originalPath: path.join(uploads, "busy.png"),
      },
      {
        id: 4,
        filename: "desk.pdf",
        status: "failed",
        uploadedAt: new Date("2026-09-29T20:00:00.000Z"),
        originalPath: path.join(outside, "secret.pdf"),
      },
    ],
    uploadsRoot: uploads,
    jobsRoot,
    backupDir,
    databasePath,
    backupDatabase: (dest) => {
      backups += 1;
      backupDatabaseFile((sql) => sqlite.exec(sql), dest);
    },
    deleteJob: (id) => {
      removed.push(id);
    },
    log: (line) => logs.push(line),
  });

  assert.deepEqual(removed.sort((a, b) => a - b), [1, 4]);
  assert.equal(report.removedJobs, 2);
  assert.equal(report.keptToday, 1);
  assert.equal(report.keptProcessing, 1);
  assert.equal(report.backupCreated, true);
  assert.equal(fs.existsSync(path.join(uploads, "old.png")), false);
  assert.equal(fs.existsSync(path.join(uploads, "quick-print", "1")), false);
  assert.equal(fs.existsSync(path.join(uploads, "thumbs", "1.jpg")), false);
  assert.equal(fs.existsSync(path.join(uploads, "1_press_ready_precompile.pdf")), false);
  assert.equal(fs.existsSync(path.join(jobsRoot, "old.txt")), false);
  assert.equal(fs.readFileSync(path.join(uploads, "today.png"), "utf8"), "today-artwork");
  assert.equal(fs.readFileSync(path.join(uploads, "busy.png"), "utf8"), "still-printing");
  assert.equal(fs.readFileSync(path.join(uploads, "12_keep.pdf"), "utf8"), "other-job");
  assert.equal(fs.readFileSync(path.join(outside, "secret.pdf"), "utf8"), "do-not-touch");
  assert.equal(fs.readFileSync(path.join(outside, "nested", "note.txt"), "utf8"), "do-not-touch-dir");
  assert.equal(fs.readFileSync(path.join(root, "uploads-evil", "file.pdf"), "utf8"), "sibling");
  assert.equal(fs.readFileSync(backupAlready, "utf8"), "backup-copy");
  assert.equal(fs.existsSync(path.join(uploads, "link-out.pdf")), false);
  assert.ok(fs.statSync(databasePath).size > 0);
  const copy = path.join(backupDir, "flyerz-before-first-cleanup.sqlite");
  assert.ok(fs.statSync(copy).size > 0);
  const opened = new DatabaseSync(copy);
  const row = opened.prepare("SELECT filename FROM file_jobs").get() as { filename: string };
  assert.equal(row.filename, "kept-in-backup");
  opened.close();
  sqlite.close();

  const again = await runJobCleanup({
    now: NOW,
    reason: "hourly",
    enabled: true,
    jobs: [],
    uploadsRoot: uploads,
    jobsRoot,
    backupDir,
    databasePath,
    backupDatabase: () => {
      backups += 1;
    },
    deleteJob: () => {
      throw new Error("nothing left to delete");
    },
  });
  assert.equal(again.backupCreated, false);
  assert.equal(backups, 1);
  assert.match(logs.join("\n"), /Removed job 1/);
  assert.match(logs.join("\n"), /still processing/);
  assert.ok(report.bytesRemoved > 0);
});

test("cleanup stays off when the setting is switched off", async () => {
  const root = appDir();
  const settings = path.join(root, "job-cleanup-settings.json");
  assert.equal(readJobCleanupSettings(path.join(root, "missing.json")).enabled, true);
  assert.equal(writeJobCleanupSettings(false, settings).enabled, false);
  assert.equal(readJobCleanupSettings(settings).enabled, false);
  writeJobCleanupSettings(true, settings);
  assert.equal(readJobCleanupSettings(settings).enabled, true);

  const uploads = path.join(root, "uploads");
  write(path.join(uploads, "old.png"), "stay");
  let called = false;
  const report = await runJobCleanup({
    now: NOW,
    enabled: false,
    reason: "startup",
    jobs: [{
      id: 9,
      filename: "old.png",
      status: "complete",
      uploadedAt: "2026-09-28 08:00:00",
      originalPath: path.join(uploads, "old.png"),
    }],
    uploadsRoot: uploads,
    jobsRoot: path.join(root, "jobs"),
    backupDir: path.join(root, "data", "backups"),
    databasePath: path.join(root, "data", "flyerz.sqlite"),
    backupDatabase: () => {
      called = true;
    },
    deleteJob: () => {
      called = true;
    },
  });
  assert.equal(report.enabled, false);
  assert.equal(report.removedJobs, 0);
  assert.equal(called, false);
  assert.equal(fs.readFileSync(path.join(uploads, "old.png"), "utf8"), "stay");
});

test("Johannesburg midnight keeps today and the next cleanup is just after midnight", () => {
  const start = startOfTodayJohannesburg(NOW);
  assert.equal(start.toISOString(), "2026-09-29T22:00:00.000Z");
  const plan = planCleanup([
    { id: 1, filename: "late.png", status: "complete", uploadedAt: "2026-09-29 21:59:00" },
    { id: 2, filename: "midnight.png", status: "complete", uploadedAt: "2026-09-29 22:00:00" },
    { id: 3, filename: "busy.png", status: "processing", uploadedAt: "2026-09-29 10:00:00" },
  ], NOW);
  assert.deepEqual(plan.remove.map((job) => job.id), [1]);
  assert.deepEqual(plan.keepToday.map((job) => job.id), [2]);
  assert.deepEqual(plan.keepProcessing.map((job) => job.id), [3]);

  const justAfter = new Date("2026-09-29T22:02:00.000Z");
  assert.equal(msUntilJustAfterMidnight(justAfter), 3 * 60 * 1000);
  const morning = new Date("2026-09-30T06:00:00.000Z");
  assert.equal(msUntilJustAfterMidnight(morning), 16 * 60 * 60 * 1000 + 5 * 60 * 1000);
});

test("the cleanup setting is on until someone switches it off", async () => {
  const root = appDir();
  const previous = process.env.JOB_CLEANUP_SETTINGS_PATH;
  process.env.JOB_CLEANUP_SETTINGS_PATH = path.join(root, "job-cleanup-settings.json");
  const app = express();
  app.use(express.json());
  registerJobCleanupRoutes(app);
  const server = http.createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  try {
    const first = await fetch(`http://127.0.0.1:${port}/api/job-cleanup/settings`);
    assert.equal((await first.json()).enabled, true);
    const saved = await fetch(`http://127.0.0.1:${port}/api/job-cleanup/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: false }),
    });
    assert.equal((await saved.json()).enabled, false);
    const again = await fetch(`http://127.0.0.1:${port}/api/job-cleanup/settings`);
    assert.equal((await again.json()).enabled, false);
  } finally {
    server.close();
    if (previous === undefined) delete process.env.JOB_CLEANUP_SETTINGS_PATH;
    else process.env.JOB_CLEANUP_SETTINGS_PATH = previous;
  }
});
