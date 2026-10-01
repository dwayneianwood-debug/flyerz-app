/** Cover-crop frame for AI images whose shape does not match the print size. */

export type FitChoice = "crop" | "extend" | "border" | "none";

export interface CropFrame {
  left: number;
  top: number;
  width: number;
  height: number;
  axis: "x" | "y" | "none";
  safeX: number;
  safeY: number;
}

const RATIO_TOLERANCE = 0.015;

export function ratiosDiffer(srcW: number, srcH: number, trimW: number, trimH: number): boolean {
  if (Math.min(srcW, srcH, trimW, trimH) <= 0) return false;
  const src = srcW / srcH;
  const target = trimW / trimH;
  return Math.abs(src - target) / target > RATIO_TOLERANCE;
}

/** Percentages of the source image. offset 0.5 is the centred cover crop. */
export function coverCropFrame(srcW: number, srcH: number, trimW: number, trimH: number, offset = 0.5): CropFrame {
  const clamped = Math.min(1, Math.max(0, offset));
  const safeX = trimW > 0 ? (5 / trimW) * 100 : 0;
  const safeY = trimH > 0 ? (5 / trimH) * 100 : 0;
  if (!ratiosDiffer(srcW, srcH, trimW, trimH)) {
    return { left: 0, top: 0, width: 100, height: 100, axis: "none", safeX, safeY };
  }
  const src = srcW / srcH;
  const target = trimW / trimH;
  if (src > target) {
    const width = (target / src) * 100;
    const slack = 100 - width;
    return { left: slack * clamped, top: 0, width, height: 100, axis: "x", safeX, safeY };
  }
  const height = (src / target) * 100;
  const slack = 100 - height;
  return { left: 0, top: slack * clamped, width: 100, height, axis: "y", safeX, safeY };
}

export function offsetFromPointer(axis: "x" | "y" | "none", pointerRatio: number, frame: CropFrame): number {
  if (axis === "none") return 0.5;
  const size = axis === "x" ? frame.width : frame.height;
  const slack = 100 - size;
  if (slack <= 0.01) return 0.5;
  const pos = pointerRatio * 100 - size / 2;
  return Math.min(1, Math.max(0, pos / slack));
}
