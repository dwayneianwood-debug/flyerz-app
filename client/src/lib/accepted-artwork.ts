/** File types the print pipeline accepts. Illustrator .ai and .eps join the PDF path. */

export const ARTWORK_DROPZONE_ACCEPT: Record<string, string[]> = {
  "application/pdf": [".pdf", ".ai"],
  "image/jpeg": [".jpg", ".jpeg"],
  "image/png": [".png"],
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
  "application/postscript": [".ai", ".eps"],
  "application/illustrator": [".ai"],
  "application/vnd.adobe.illustrator": [".ai"],
  "image/x-eps": [".eps"],
  "application/eps": [".eps"],
  "application/octet-stream": [".ai", ".eps"],
};

export const PRINT_TOOL_ACCEPT = ".pdf,.ai,.eps,.jpg,.jpeg,.png";

export const ARTWORK_TYPE_LABEL = "PDF, AI, EPS, JPG, PNG, DOCX, PPTX";
export const PRINT_TOOL_TYPE_LABEL = "PDF, AI, EPS, JPG, PNG";

export function fileExtension(name: string): string {
  return name.split(".").pop()?.toLowerCase() || "";
}

export function isPrintToolFile(name: string): boolean {
  return ["pdf", "jpg", "jpeg", "png", "ai", "eps"].includes(fileExtension(name));
}

export function isIllustratorFile(name: string): boolean {
  const ext = fileExtension(name);
  return ext === "ai" || ext === "eps";
}
