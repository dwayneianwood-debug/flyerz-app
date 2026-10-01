export type ColourBorderSource = "preset" | "custom" | "edge";

export type ColourBorderChoice = {
  source: ColourBorderSource;
  presetId: string;
  label: string;
  c: number;
  m: number;
  y: number;
  k: number;
  r: number;
  g: number;
  b: number;
};

export const COLOUR_BORDER_PRESETS: Array<{
  id: string;
  label: string;
  c: number;
  m: number;
  y: number;
  k: number;
  r: number;
  g: number;
  b: number;
}> = [
  { id: "white", label: "White", c: 0, m: 0, y: 0, k: 0, r: 255, g: 255, b: 255 },
  { id: "black", label: "Black", c: 0, m: 0, y: 0, k: 100, r: 0, g: 0, b: 0 },
  { id: "richBlack", label: "Rich Black", c: 40, m: 30, y: 30, k: 100, r: 0, g: 0, b: 0 },
  { id: "cyan", label: "Cyan", c: 100, m: 0, y: 0, k: 0, r: 0, g: 255, b: 255 },
  { id: "magenta", label: "Magenta", c: 0, m: 100, y: 0, k: 0, r: 255, g: 0, b: 255 },
  { id: "yellow", label: "Yellow", c: 0, m: 0, y: 100, k: 0, r: 255, g: 255, b: 0 },
  { id: "red", label: "Red", c: 0, m: 100, y: 100, k: 0, r: 255, g: 0, b: 0 },
  { id: "blue", label: "Blue", c: 100, m: 100, y: 0, k: 0, r: 0, g: 0, b: 255 },
  { id: "green", label: "Green", c: 100, m: 0, y: 100, k: 0, r: 0, g: 255, b: 0 },
];

const SESSION_KEY = "flyerz-colour-border";

export function clampInk(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

export function cmykToRgb(c: number, m: number, y: number, k: number): { r: number; g: number; b: number } {
  const C = clampInk(c) / 100;
  const M = clampInk(m) / 100;
  const Y = clampInk(y) / 100;
  const K = clampInk(k) / 100;
  return {
    r: Math.round(255 * (1 - C) * (1 - K)),
    g: Math.round(255 * (1 - M) * (1 - K)),
    b: Math.round(255 * (1 - Y) * (1 - K)),
  };
}

export function rgbToCmyk(r: number, g: number, b: number): { c: number; m: number; y: number; k: number } {
  const rn = Math.min(255, Math.max(0, r)) / 255;
  const gn = Math.min(255, Math.max(0, g)) / 255;
  const bn = Math.min(255, Math.max(0, b)) / 255;
  const k = 1 - Math.max(rn, gn, bn);
  if (k >= 0.999) return { c: 0, m: 0, y: 0, k: 100 };
  return {
    c: Math.round(((1 - rn - k) / (1 - k)) * 1000) / 10,
    m: Math.round(((1 - gn - k) / (1 - k)) * 1000) / 10,
    y: Math.round(((1 - bn - k) / (1 - k)) * 1000) / 10,
    k: Math.round(k * 1000) / 10,
  };
}

export function rgbHex(r: number, g: number, b: number): string {
  const hex = (n: number) => Math.min(255, Math.max(0, Math.round(n))).toString(16).padStart(2, "0");
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

export function presetChoice(id: string): ColourBorderChoice {
  const preset = COLOUR_BORDER_PRESETS.find((item) => item.id === id) || COLOUR_BORDER_PRESETS[0];
  return { source: "preset", presetId: preset.id, label: preset.label, c: preset.c, m: preset.m, y: preset.y, k: preset.k, r: preset.r, g: preset.g, b: preset.b };
}

export function customFromRgb(r: number, g: number, b: number): ColourBorderChoice {
  const ink = rgbToCmyk(r, g, b);
  const rgb = cmykToRgb(ink.c, ink.m, ink.y, ink.k);
  return { source: "custom", presetId: "custom", label: "Custom", ...ink, ...rgb };
}

export function customFromCmyk(c: number, m: number, y: number, k: number): ColourBorderChoice {
  const ink = { c: clampInk(c), m: clampInk(m), y: clampInk(y), k: clampInk(k) };
  return { source: "custom", presetId: "custom", label: "Custom", ...ink, ...cmykToRgb(ink.c, ink.m, ink.y, ink.k) };
}

export function loadColourBorderChoice(): ColourBorderChoice {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return presetChoice("white");
    const parsed = JSON.parse(raw) as Partial<ColourBorderChoice>;
    if (parsed.source === "preset" && parsed.presetId) return presetChoice(parsed.presetId);
    if (parsed.source === "edge") {
      return {
        source: "edge",
        presetId: "edge",
        label: "Match artwork edge",
        c: clampInk(Number(parsed.c)),
        m: clampInk(Number(parsed.m)),
        y: clampInk(Number(parsed.y)),
        k: clampInk(Number(parsed.k)),
        r: Number(parsed.r) || 0,
        g: Number(parsed.g) || 0,
        b: Number(parsed.b) || 0,
      };
    }
    return customFromCmyk(Number(parsed.c), Number(parsed.m), Number(parsed.y), Number(parsed.k));
  } catch {
    return presetChoice("white");
  }
}

export function saveColourBorderChoice(choice: ColourBorderChoice): void {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(choice));
  } catch {
    /* session storage can be unavailable */
  }
}
