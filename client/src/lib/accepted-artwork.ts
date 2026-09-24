/** Dropzone and labels. Extensions come from shared/artwork-types.json. */

import {
  ARTWORK_TYPE_LABEL,
  PRINT_TOOL_ACCEPT,
  PRINT_TOOL_TYPE_LABEL,
  UPLOAD_EXTENSIONS,
  isIllustratorFile,
  isPrintToolExtension,
  isVectorArtwork,
} from "@shared/artwork-types";

export {
  ARTWORK_TYPE_LABEL,
  PRINT_TOOL_ACCEPT,
  PRINT_TOOL_TYPE_LABEL,
  isIllustratorFile,
  isVectorArtwork,
};

const MIME_FOR_EXT: Record<string, string[]> = {
  ".pdf": ["application/pdf"],
  ".jpg": ["image/jpeg"],
  ".jpeg": ["image/jpeg"],
  ".png": ["image/png"],
  ".docx": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  ".pptx": ["application/vnd.openxmlformats-officedocument.presentationml.presentation"],
  ".ai": ["application/pdf", "application/postscript", "application/illustrator", "application/vnd.adobe.illustrator"],
  ".eps": ["application/postscript", "image/x-eps", "application/eps"],
};

function addExtension(accept: Record<string, string[]>, mime: string, ext: string) {
  const current = accept[mime] || [];
  if (!current.includes(ext)) accept[mime] = [...current, ext];
}

export const ARTWORK_DROPZONE_ACCEPT: Record<string, string[]> = (() => {
  const accept: Record<string, string[]> = {};
  for (const ext of UPLOAD_EXTENSIONS) {
    for (const mime of MIME_FOR_EXT[ext] || ["application/octet-stream"]) {
      addExtension(accept, mime, ext);
    }
  }
  addExtension(accept, "application/octet-stream", ".ai");
  addExtension(accept, "application/octet-stream", ".eps");
  return accept;
})();

export function fileExtension(name: string): string {
  const base = (name || "").split(/[/\\]/).pop() || "";
  if (!base.includes(".")) return base.toLowerCase();
  return base.split(".").pop()?.toLowerCase() || "";
}

export function isPrintToolFile(name: string): boolean {
  return isPrintToolExtension(fileExtension(name));
}
