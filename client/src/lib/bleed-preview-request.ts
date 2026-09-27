import { normalizeBleedMm } from "@shared/bleed-size";

/** Build the bleed-preview query. A click event is not a strategy, so Retry can call this safely. */
export function bleedPreviewQuery(strategy: unknown, bleedMm: unknown): string {
  const params = new URLSearchParams();
  if (typeof strategy === "string" && strategy && strategy !== "auto") {
    params.set("strategy", strategy);
  }
  params.set("bleed", String(normalizeBleedMm(bleedMm)));
  return `?${params.toString()}`;
}
