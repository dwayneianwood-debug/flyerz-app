export const UPLOAD_LIMIT_BYTES = 500 * 1024 * 1024;
export const UPLOAD_LIMIT_MB = 500;

export function uploadTooLargeMessage(bytes: number): string {
  const mb = Math.max(1, Math.round(Number(bytes) / (1024 * 1024)));
  return `This file is ${mb} MB; the limit is ${UPLOAD_LIMIT_MB} MB`;
}
