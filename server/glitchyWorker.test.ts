import { test } from "node:test";
import assert from "node:assert/strict";

import {
  GlitchyWorker,
  glitchyDedupeKey,
  type GlitchyFeedback,
  type GlitchyWorkerOptions,
} from "./glitchyWorker";

/** Deterministic harness: manual clock + manual timer queue. */
function makeHarness(overrides: Partial<GlitchyWorkerOptions> = {}) {
  let now = 0;
  const timers: Array<() => void> = [];
  const calls: GlitchyFeedback[] = [];

  const base: GlitchyWorkerOptions = {
    enabled: true,
    minIntervalMs: 0,
    maxConcurrent: 5,
    maxQueue: 50,
    dedupeWindowMs: 60_000,
    trigger: (item) => {
      calls.push(item);
    },
    now: () => now,
    schedule: (cb) => {
      timers.push(cb);
    },
  };

  const worker = new GlitchyWorker({ ...base, ...overrides });

  /** Run one generation of scheduled callbacks (not ones they enqueue). */
  const flush = () => {
    const batch = timers.splice(0, timers.length);
    for (const cb of batch) cb();
  };

  // Lets queued microtasks (.finally cleanup) run before the next flush.
  const tick = () => new Promise<void>((r) => setImmediate(r));

  return {
    worker,
    calls,
    flush,
    tick,
    setNow: (t: number) => {
      now = t;
    },
  };
}

/** A trigger whose completion the test controls. */
function deferredTrigger() {
  const resolvers: Array<() => void> = [];
  const seen: GlitchyFeedback[] = [];
  const trigger = (item: GlitchyFeedback) =>
    new Promise<void>((resolve) => {
      seen.push(item);
      resolvers.push(resolve);
    });
  return {
    trigger,
    seen,
    resolveNext: () => {
      const r = resolvers.shift();
      if (r) r();
    },
  };
}

test("dedupe key normalizes whitespace/case and includes jobId", () => {
  assert.equal(
    glitchyDedupeKey({ userFeedback: "  Bleed   Looks  OFF ", jobId: 7 }),
    "7::bleed looks off",
  );
  assert.equal(
    glitchyDedupeKey({ userFeedback: "x" }),
    "::x",
  );
});

test("disabled worker never triggers", async () => {
  const h = makeHarness({ enabled: false });
  const res = h.worker.enqueue({ userFeedback: "fix bleed" });
  assert.equal(res.accepted, false);
  assert.equal(res.reason, "disabled");
  h.flush();
  await h.tick();
  assert.equal(h.calls.length, 0);
});

test("empty feedback is rejected", () => {
  const h = makeHarness();
  const res = h.worker.enqueue({ userFeedback: "   " });
  assert.equal(res.accepted, false);
  assert.equal(res.reason, "empty");
});

test("duplicate feedback within window is rejected and triggers once", async () => {
  const h = makeHarness();
  const first = h.worker.enqueue({ userFeedback: "bleed off", jobId: 1 });
  const second = h.worker.enqueue({ userFeedback: "BLEED   off", jobId: 1 });
  assert.equal(first.accepted, true);
  assert.equal(second.accepted, false);
  assert.equal(second.reason, "duplicate");
  h.flush();
  await h.tick();
  assert.equal(h.calls.length, 1);
});

test("distinct feedback is not deduped", async () => {
  const h = makeHarness();
  assert.equal(h.worker.enqueue({ userFeedback: "a", jobId: 1 }).accepted, true);
  assert.equal(h.worker.enqueue({ userFeedback: "b", jobId: 1 }).accepted, true);
  assert.equal(h.worker.enqueue({ userFeedback: "a", jobId: 2 }).accepted, true);
  h.flush();
  await h.tick();
  assert.equal(h.calls.length, 3);
});

test("queue cap rejects overflow beyond maxQueue", () => {
  // maxConcurrent 1 + never-resolving trigger => 1 in-flight, rest queue.
  const d = deferredTrigger();
  const h = makeHarness({ maxConcurrent: 1, maxQueue: 1, trigger: d.trigger });

  assert.equal(h.worker.enqueue({ userFeedback: "one" }).accepted, true);
  h.flush(); // "one" leaves the queue and becomes in-flight
  assert.equal(h.worker.enqueue({ userFeedback: "two" }).accepted, true); // queued (len 1)
  const third = h.worker.enqueue({ userFeedback: "three" }); // len 1 >= maxQueue 1
  assert.equal(third.accepted, false);
  assert.equal(third.reason, "queue_full");
});

test("concurrency cap limits simultaneous in-flight triggers", async () => {
  const d = deferredTrigger();
  const h = makeHarness({ maxConcurrent: 2, trigger: d.trigger });

  h.worker.enqueue({ userFeedback: "a" });
  h.worker.enqueue({ userFeedback: "b" });
  h.worker.enqueue({ userFeedback: "c" });
  h.flush();
  await h.tick();

  // Only 2 may run; the third waits.
  assert.equal(d.seen.length, 2);
  assert.equal(h.worker.inFlight, 2);
  assert.equal(h.worker.pending, 1);

  // Finishing one frees a slot and drains the third.
  d.resolveNext();
  await h.tick();
  h.flush();
  await h.tick();
  assert.equal(d.seen.length, 3);
});

test("rate cap allows at most one trigger per interval", async () => {
  const h = makeHarness({ minIntervalMs: 1000, maxConcurrent: 5 });

  h.setNow(0);
  h.worker.enqueue({ userFeedback: "a" });
  h.flush();
  await h.tick();
  assert.equal(h.calls.length, 1);

  // Second within the interval must wait.
  h.worker.enqueue({ userFeedback: "b" });
  h.flush();
  await h.tick();
  assert.equal(h.calls.length, 1);

  // After the interval elapses it fires.
  h.setNow(1000);
  h.flush();
  await h.tick();
  assert.equal(h.calls.length, 2);
});

test("happy path maps feedback fields through to the trigger", async () => {
  const h = makeHarness();
  h.worker.enqueue({
    userFeedback: "bleed offset",
    jobId: 208,
    cropBox: [0, 0, 1, 1],
    gsLogs: "gs tail",
    pageState: { page: "/job/208" },
  });
  h.flush();
  await h.tick();

  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.calls[0].cropBox, [0, 0, 1, 1]);
  assert.equal(h.calls[0].jobId, 208);
  assert.equal(h.calls[0].userFeedback, "bleed offset");
});
