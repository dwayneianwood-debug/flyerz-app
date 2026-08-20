/**
 * Full-page crop_box for the "No Crop Needed" route.
 * Normalized percentages (0–1) covering the entire artwork.
 * Must be populated whenever the user skips drawing a crop, so the backend
 * never sees crop_box=None (Document Closed / stuck job).
 */
export const FULL_PAGE_CROP_BOX = {
  cropX: 0,
  cropY: 0,
  cropWidth: 1,
  cropHeight: 1,
} as const;

export type CropBoxCoords = {
  cropX: number;
  cropY: number;
  cropWidth: number;
  cropHeight: number;
};

const EPS = 1e-6;

export function isFullPageCropBox(
  crop: Partial<CropBoxCoords> | null | undefined,
): boolean {
  if (!crop) return false;
  const x = Number(crop.cropX);
  const y = Number(crop.cropY);
  const w = Number(crop.cropWidth);
  const h = Number(crop.cropHeight);
  if (![x, y, w, h].every((n) => Number.isFinite(n))) return false;
  return Math.abs(x) < EPS && Math.abs(y) < EPS && Math.abs(w - 1) < EPS && Math.abs(h - 1) < EPS;
}

/** True when the user drew a real crop region (not missing, not full-page). */
export function hasUserManualCrop(
  crop: Partial<CropBoxCoords> | null | undefined,
): boolean {
  if (!crop) return false;
  const w = Number(crop.cropWidth);
  const h = Number(crop.cropHeight);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return false;
  if (crop.cropX == null || crop.cropY == null) return false;
  return !isFullPageCropBox(crop);
}

/** Always return a valid crop_box; default to full-page when missing/invalid. */
export function ensureFullPageCropBox(
  crop?: Partial<CropBoxCoords> | null,
): CropBoxCoords {
  const x = Number(crop?.cropX);
  const y = Number(crop?.cropY);
  const w = Number(crop?.cropWidth);
  const h = Number(crop?.cropHeight);
  if (
    crop &&
    Number.isFinite(x) &&
    Number.isFinite(y) &&
    Number.isFinite(w) &&
    Number.isFinite(h) &&
    w > 0 &&
    h > 0
  ) {
    return { cropX: x, cropY: y, cropWidth: w, cropHeight: h };
  }
  return { ...FULL_PAGE_CROP_BOX };
}
