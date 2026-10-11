/** Layout numbers shared by Chrome, Safari, and Edge. No browser-specific branch. */

export const PHONE_MAX_WIDTH = 768;
export const PANEL_GUTTER = 12;

export function chatPanelLayout(viewportWidth: number): { width: number; marginLeft: number } {
  const width = viewportWidth <= PHONE_MAX_WIDTH
    ? Math.min(240, Math.max(0, viewportWidth - 24))
    : 330;
  const marginLeft = viewportWidth <= PHONE_MAX_WIDTH ? 0 : -105;
  return { width, marginLeft };
}

export function speechBubbleMaxWidth(viewportWidth: number): number {
  if (viewportWidth <= PHONE_MAX_WIDTH) {
    return Math.min(220, Math.max(0, viewportWidth - 32));
  }
  return 240;
}

/** The chat panel stays inside the screen, including a 12px gutter on each side. */
export function chatPanelInside(viewportWidth: number, anchorRight = PANEL_GUTTER): boolean {
  const { width, marginLeft } = chatPanelLayout(viewportWidth);
  const right = viewportWidth - anchorRight;
  const left = right - width + marginLeft;
  return left >= PANEL_GUTTER && right <= viewportWidth - PANEL_GUTTER && width > 0 && width <= viewportWidth - 24;
}

/** Lift the cat above a home indicator. The 12px anchor stays, and the inset is added. */
export function anchoredBottom(placementBottom: number, safeAreaBottom: number): number {
  const inset = Number.isFinite(safeAreaBottom) ? Math.max(0, safeAreaBottom) : 0;
  return placementBottom + inset;
}
