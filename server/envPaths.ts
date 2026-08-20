import fs from "fs";
import path from "path";

/** Matches Python `_init_fai_temp_dir`: env override, then Linux RAM disk, then repo temp. */
export function getFlyerzTempRoot(): string {
  const env = process.env.FAI_TEMP_DIR?.trim();
  if (env) return path.resolve(env);
  const shmMount = path.join(path.sep, "dev", "shm");
  const shmFlyerz = path.join(shmMount, "flyerz_tmp");
  try {
    if (fs.existsSync(shmMount)) {
      fs.mkdirSync(shmFlyerz, { recursive: true });
      return shmFlyerz;
    }
  } catch {
    /* fall through */
  }
  return path.join(process.cwd(), "fai_temp_processing");
}
