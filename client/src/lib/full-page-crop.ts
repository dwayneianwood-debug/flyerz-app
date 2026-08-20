/** Normalized full-page crop_box for the "No Crop Needed" route (percent of page). */
export const FULL_PAGE_CROP_BOX = {
  cropX: 0,
  cropY: 0,
  cropWidth: 1,
  cropHeight: 1,
} as const;

export function isFullPageCropBox(crop: {
  cropX?: number;
  cropY?: number;
  cropWidth?: number;
  cropHeight?: number;
} | null | undefined): boolean {
  if (!crop) return false;
  return crop.cropX === 0 && crop.cropY === 0 && crop.cropWidth === 1 && crop.cropHeight === 1;
}
