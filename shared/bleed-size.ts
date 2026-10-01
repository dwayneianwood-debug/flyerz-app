/** Press bleed the customer can choose. 5 mm is the default. */
export const BLEED_PRESETS_MM = [3, 5, 10] as const;
export const DEFAULT_BLEED_MM = 5;
export const MIN_BLEED_MM = 1;
export const MAX_BLEED_MM = 25;

export function normalizeBleedMm(value: unknown): number {
  const number = typeof value === "number" ? value : parseFloat(String(value ?? ""));
  if (!Number.isFinite(number)) return DEFAULT_BLEED_MM;
  const rounded = Math.round(number * 10) / 10;
  if (rounded < MIN_BLEED_MM || rounded > MAX_BLEED_MM) return DEFAULT_BLEED_MM;
  return rounded;
}
