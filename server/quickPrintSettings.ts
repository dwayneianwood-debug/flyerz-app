import fs from "fs";
import path from "path";
import { QUICK_PRINT_DEFAULT_PRODUCT_ID, quickPrintProduct } from "@shared/quickPrint";

export interface QuickPrintSettings {
  enabled: boolean;
  folderPath: string;
  defaultProductId: string;
}

export function defaultQuickPrintSettings(): QuickPrintSettings {
  return {
    enabled: false,
    folderPath: "",
    defaultProductId: QUICK_PRINT_DEFAULT_PRODUCT_ID,
  };
}

export function quickPrintSettingsPath(): string {
  return process.env.QUICK_PRINT_SETTINGS_PATH
    ? path.resolve(process.env.QUICK_PRINT_SETTINGS_PATH)
    : path.join(process.cwd(), "data", "quick-print-settings.json");
}

export function readQuickPrintSettings(): QuickPrintSettings {
  const defaults = defaultQuickPrintSettings();
  try {
    const raw = fs.readFileSync(quickPrintSettingsPath(), "utf8");
    const parsed = JSON.parse(raw) as Partial<QuickPrintSettings>;
    const productId = quickPrintProduct(parsed.defaultProductId) ? String(parsed.defaultProductId) : defaults.defaultProductId;
    return {
      enabled: parsed.enabled === true,
      folderPath: typeof parsed.folderPath === "string" ? parsed.folderPath.trim() : "",
      defaultProductId: productId,
    };
  } catch {
    return defaults;
  }
}

export function writeQuickPrintSettings(input: Partial<QuickPrintSettings>): QuickPrintSettings {
  const current = readQuickPrintSettings();
  const next: QuickPrintSettings = {
    enabled: input.enabled === undefined ? current.enabled : input.enabled === true,
    folderPath: input.folderPath === undefined ? current.folderPath : String(input.folderPath || "").trim(),
    defaultProductId: quickPrintProduct(input.defaultProductId || current.defaultProductId)
      ? String(input.defaultProductId || current.defaultProductId)
      : current.defaultProductId,
  };
  if (next.enabled && !next.folderPath) {
    throw new Error("Choose a folder before turning the drop folder on.");
  }
  if (next.enabled && !path.isAbsolute(next.folderPath)) {
    throw new Error("The drop folder has to be a full path, for example C:\\Flyerz\\PrintReady.");
  }
  const filePath = quickPrintSettingsPath();
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(next, null, 2));
  return next;
}
