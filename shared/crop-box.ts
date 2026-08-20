/** Normalized full-page crop_box (percent of raster). Values ≤ 1.0 are treated as fractions. */
export const FULL_PAGE_CROP_NORMALIZED = {
  cropX: 0,
  cropY: 0,
  cropWidth: 1,
  cropHeight: 1,
} as const;

export type CropBoxDict = {
  cropX: number;
  cropY: number;
  cropWidth: number;
  cropHeight: number;
};

export function hasValidCropBox(opts: Record<string, any> | null | undefined): boolean {
  if (!opts) return false;
  const w = Number(opts.cropWidth);
  const h = Number(opts.cropHeight);
  return (
    opts.cropX != null &&
    opts.cropY != null &&
    Number.isFinite(w) &&
    w > 0 &&
    Number.isFinite(h) &&
    h > 0
  );
}

export function isNoCropRoute(opts: Record<string, any> | null | undefined): boolean {
  if (!opts) return false;
  return opts.preserveBleed === true || opts.isNoCrop === true || opts.is_no_crop === true;
}

/**
 * Populate crop_box with full-page dimensions.
 * Default: only for the No Crop Needed route (preserveBleed / isNoCrop).
 * force=true: fill any missing crop (compile/precompile Document Closed safety).
 */
export function ensureFullPageCropBox<T extends Record<string, any>>(
  opts: T | null | undefined,
  options: { force?: boolean } = {},
): T & CropBoxDict & { isNoCrop?: boolean } {
  const result = { ...(opts || {}) } as T & CropBoxDict & { isNoCrop?: boolean };
  if (hasValidCropBox(result)) {
    if (isNoCropRoute(result)) result.isNoCrop = true;
    return result;
  }
  const shouldFill = options.force === true || isNoCropRoute(result);
  if (!shouldFill) {
    return result;
  }
  result.cropX = FULL_PAGE_CROP_NORMALIZED.cropX;
  result.cropY = FULL_PAGE_CROP_NORMALIZED.cropY;
  result.cropWidth = FULL_PAGE_CROP_NORMALIZED.cropWidth;
  result.cropHeight = FULL_PAGE_CROP_NORMALIZED.cropHeight;
  if (isNoCropRoute(result)) {
    result.isNoCrop = true;
  }
  return result;
}
