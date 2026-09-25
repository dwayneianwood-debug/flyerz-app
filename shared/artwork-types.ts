import catalog from "./artwork-types.json";

export const UPLOAD_EXTENSIONS = catalog.uploadExtensions;
export const PRINT_TOOL_EXTENSIONS = catalog.printToolExtensions;
export const VECTOR_EXTENSIONS = catalog.vectorExtensions;
export const RASTER_EXTENSIONS = catalog.rasterExtensions;
export const VECTOR_TYPES = catalog.vectorTypes;
export const ILLUSTRATOR_TYPES = catalog.illustratorTypes;

const DISPLAY_NAMES: Record<string, string> = catalog.displayNames;

function withDot(value: string): string {
  const raw = (value || "").trim().toLowerCase();
  if (!raw) return "";
  return raw.startsWith(".") ? raw : `.${raw}`;
}

function bareType(value: string): string {
  const base = (value || "").split(/[/\\]/).pop() || "";
  if (!base.includes(".")) return base.toLowerCase();
  return base.split(".").pop()?.toLowerCase() || "";
}

export function typeLabel(extensions: readonly string[]): string {
  const seen = new Set<string>();
  const parts: string[] = [];
  for (const ext of extensions) {
    if (ext === ".jpeg") continue;
    const name = DISPLAY_NAMES[ext] || ext.replace(/^\./, "").toUpperCase();
    if (seen.has(name)) continue;
    seen.add(name);
    parts.push(name);
  }
  return parts.join(", ");
}

export const ARTWORK_TYPE_LABEL = typeLabel(UPLOAD_EXTENSIONS);
export const PRINT_TOOL_TYPE_LABEL = typeLabel(PRINT_TOOL_EXTENSIONS);
export const PRINT_TOOL_ACCEPT = PRINT_TOOL_EXTENSIONS.join(",");

export function isUploadExtension(ext: string): boolean {
  return (UPLOAD_EXTENSIONS as readonly string[]).includes(withDot(ext));
}

export function isPrintToolExtension(ext: string): boolean {
  return (PRINT_TOOL_EXTENSIONS as readonly string[]).includes(withDot(ext));
}

export function isVectorExtension(ext: string): boolean {
  return (VECTOR_EXTENSIONS as readonly string[]).includes(withDot(ext));
}

export function isRasterExtension(ext: string): boolean {
  return (RASTER_EXTENSIONS as readonly string[]).includes(withDot(ext));
}

export function isPassThroughExtension(ext: string): boolean {
  return isVectorExtension(ext) || isRasterExtension(ext);
}

export function isVectorType(fileType: string): boolean {
  return (VECTOR_TYPES as readonly string[]).includes(bareType(fileType));
}

export function isIllustratorType(fileType: string): boolean {
  return (ILLUSTRATOR_TYPES as readonly string[]).includes(bareType(fileType));
}

export function isPrintToolType(fileType: string): boolean {
  return isPrintToolExtension(bareType(fileType));
}

export function isVectorArtwork(nameOrType: string): boolean {
  return isVectorType(nameOrType);
}

export function isIllustratorFile(nameOrType: string): boolean {
  return isIllustratorType(nameOrType);
}
