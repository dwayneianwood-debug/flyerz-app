export type CropBox = { x: number; y: number; w: number; h: number };

export type RatioId =
  | "free"
  | "original"
  | "square"
  | "a5-portrait"
  | "a5-landscape"
  | "a4-portrait"
  | "a4-landscape"
  | "a3-portrait"
  | "a3-landscape";

export const CROP_RATIOS: { id: RatioId; label: string; ratio: number | null }[] = [
  { id: "free", label: "Free", ratio: null },
  { id: "original", label: "Original", ratio: null },
  { id: "square", label: "Square", ratio: 1 },
  { id: "a5-portrait", label: "A5 portrait", ratio: 148 / 210 },
  { id: "a5-landscape", label: "A5 landscape", ratio: 210 / 148 },
  { id: "a4-portrait", label: "A4 portrait", ratio: 210 / 297 },
  { id: "a4-landscape", label: "A4 landscape", ratio: 297 / 210 },
  { id: "a3-portrait", label: "A3 portrait", ratio: 297 / 420 },
  { id: "a3-landscape", label: "A3 landscape", ratio: 420 / 297 },
];

export function pxToMm(px: number, dpi: number): number {
  const safeDpi = dpi > 1 ? dpi : 300;
  return (px / safeDpi) * 25.4;
}

export function mmToPx(mm: number, dpi: number): number {
  const safeDpi = dpi > 1 ? dpi : 300;
  return (mm / 25.4) * safeDpi;
}

export function ptToMm(pt: number): number {
  return (pt * 25.4) / 72;
}

export function ptToPx(pt: number, dpi: number): number {
  const safeDpi = dpi > 1 ? dpi : 300;
  return (pt * safeDpi) / 72;
}

export function pxToPt(px: number, dpi: number): number {
  const safeDpi = dpi > 1 ? dpi : 300;
  return (px * 72) / safeDpi;
}

export function clampCrop(crop: CropBox, boundsW: number, boundsH: number, minSize = 1): CropBox {
  const w = Math.min(Math.max(minSize, crop.w), boundsW);
  const h = Math.min(Math.max(minSize, crop.h), boundsH);
  const x = Math.min(Math.max(0, crop.x), Math.max(0, boundsW - w));
  const y = Math.min(Math.max(0, crop.y), Math.max(0, boundsH - h));
  return { x, y, w, h };
}

/** Largest centered rectangle of the given width/height ratio that fits in the bounds. */
export function centeredRatioCrop(boundsW: number, boundsH: number, ratioWH: number): CropBox {
  let w = boundsW;
  let h = w / ratioWH;
  if (h > boundsH) {
    h = boundsH;
    w = h * ratioWH;
  }
  return {
    x: (boundsW - w) / 2,
    y: (boundsH - h) / 2,
    w,
    h,
  };
}

export function croppedDownloadName(originalName: string, ext: string): string {
  const slash = Math.max(originalName.lastIndexOf("/"), originalName.lastIndexOf("\\"));
  const base = slash >= 0 ? originalName.slice(slash + 1) : originalName;
  const dot = base.lastIndexOf(".");
  const stem = (dot > 0 ? base.slice(0, dot) : base) || "artwork";
  return `${stem}_cropped.${ext}`;
}
