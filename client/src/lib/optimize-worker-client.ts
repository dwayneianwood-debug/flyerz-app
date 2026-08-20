import { getWorkerBlobUrl } from "./optimize-worker-script";

const blobCache = new Map<string, { blob: Blob; timestamp: number }>();
const CACHE_TTL_MS = 5 * 60 * 1000;

function cacheKey(file: File | Blob, fitW: number, fitH: number): string {
  const size = file.size;
  const name = file instanceof File ? file.name : "blob";
  return `${name}_${size}_${fitW}x${fitH}`;
}

function pruneCache(): void {
  const now = Date.now();
  for (const [key, entry] of blobCache) {
    if (now - entry.timestamp > CACHE_TTL_MS) {
      blobCache.delete(key);
    }
  }
}

export function optimizeImageViaWorker(
  file: File | Blob,
  targetWidthMm: number,
  targetHeightMm: number,
): Promise<Blob> {
  const DPI = 300;
  const MM_PER_INCH = 25.4;
  const targetPxW = Math.round((targetWidthMm / MM_PER_INCH) * DPI);
  const targetPxH = Math.round((targetHeightMm / MM_PER_INCH) * DPI);

  return new Promise((resolve, reject) => {
    const TIMEOUT_MS = 30000;
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) { settled = true; reject(new Error("Worker optimization timed out")); }
    }, TIMEOUT_MS);
    const finish = (fn: () => void) => { if (!settled) { settled = true; clearTimeout(timer); fn(); } };

    const img = new Image();
    const url = URL.createObjectURL(file);

    img.onload = () => {
      try {
        const srcW = img.naturalWidth;
        const srcH = img.naturalHeight;
        const srcAspect = srcW / srcH;
        const tgtAspect = targetPxW / targetPxH;
        let fitW: number, fitH: number;
        if (srcAspect > tgtAspect) {
          fitW = targetPxW;
          fitH = Math.round(targetPxW / srcAspect);
        } else {
          fitH = targetPxH;
          fitW = Math.round(targetPxH * srcAspect);
        }
        if (fitW >= srcW && fitH >= srcH) {
          fitW = srcW;
          fitH = srcH;
        }

        pruneCache();
        const key = cacheKey(file, fitW, fitH);
        const cached = blobCache.get(key);
        if (cached) {
          URL.revokeObjectURL(url);
          console.log(`[BETA-OPTIMIZE] Cache hit: ${key}`);
          finish(() => resolve(cached.blob));
          return;
        }

        if (typeof createImageBitmap === "undefined" || typeof OffscreenCanvas === "undefined") {
          URL.revokeObjectURL(url);
          finish(() => reject(new Error("OffscreenCanvas or createImageBitmap not available")));
          return;
        }

        createImageBitmap(img).then((bitmap) => {
          URL.revokeObjectURL(url);

          let worker: Worker;
          try {
            worker = new Worker(getWorkerBlobUrl());
          } catch {
            bitmap.close();
            finish(() => reject(new Error("Worker creation failed")));
            return;
          }

          worker.onmessage = (ev: MessageEvent) => {
            worker.terminate();
            if (ev.data.error) {
              finish(() => reject(new Error(ev.data.error)));
            } else {
              const blob: Blob = ev.data.blob;
              blobCache.set(key, { blob, timestamp: Date.now() });
              console.log(
                `[BETA-OPTIMIZE] Worker done: ${srcW}×${srcH} → ${fitW}×${fitH}px | ` +
                `${(file.size / 1024).toFixed(0)}KB → ${(blob.size / 1024).toFixed(0)}KB`
              );
              finish(() => resolve(blob));
            }
          };

          worker.onerror = (err) => {
            worker.terminate();
            finish(() => reject(new Error(err.message || "Worker error")));
          };

          worker.postMessage({ bitmap, fitW, fitH }, [bitmap]);
        }).catch((err) => {
          URL.revokeObjectURL(url);
          finish(() => reject(err instanceof Error ? err : new Error(String(err))));
        });
      } catch (e) {
        URL.revokeObjectURL(url);
        finish(() => reject(e instanceof Error ? e : new Error(String(e))));
      }
    };

    img.onerror = () => {
      URL.revokeObjectURL(url);
      finish(() => reject(new Error("Failed to load image for worker optimization")));
    };

    img.src = url;
  });
}
