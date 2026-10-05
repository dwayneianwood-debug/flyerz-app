import products from "./quick-print-products.json";

export type QuickLight = "green" | "amber" | "red";

export interface QuickPrintProduct {
  id: string;
  label: string;
  widthMm: number;
  heightMm: number;
}

/** Finished sizes sales can pick. A0–A7, DL and the business cards, in both orientations. */
export const QUICK_PRINT_PRODUCTS: QuickPrintProduct[] = products;

export const QUICK_PRINT_DEFAULT_PRODUCT_ID = "a5";

export function quickPrintProduct(id: string | null | undefined): QuickPrintProduct | undefined {
  return QUICK_PRINT_PRODUCTS.find((product) => product.id === id);
}

export interface QuickCheckItem {
  id: string;
  label: string;
  passed: boolean;
  detail: string;
}

export interface QuickPrintCard {
  id: number;
  filename: string;
  status: string;
  light: QuickLight | null;
  reasons: string[];
  decisions: string[];
  checklist?: QuickCheckItem[];
  clientMessage: string;
  approved: boolean;
  hasPress: boolean;
  hasProof: boolean;
  productLabel: string;
  quantity: number | null;
  notes: string;
  /** Set while quick mode is still working. */
  stage?: string;
  percent?: number;
  elapsedSec?: number;
  note?: string;
}
