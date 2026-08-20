import { sqliteTable, text, integer } from "drizzle-orm/sqlite-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod";

// === TABLE DEFINITIONS ===
export const fileJobs = sqliteTable("file_jobs", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  filename: text("filename").notNull(),
  originalPath: text("original_path").notNull(),
  correctedPath: text("corrected_path"),
  status: text("status").notNull().default("pending"), // pending, processing, complete, failed
  uploadedAt: integer("uploaded_at", { mode: "timestamp_ms" }).$defaultFn(() => new Date()).notNull(),
  completedAt: integer("completed_at", { mode: "timestamp_ms" }),
  fileSize: integer("file_size").notNull(),
  fileType: text("file_type").notNull(), // pdf, jpg, png, docx, pptx
  // SQLite has no native JSONB; store JSON as text via Drizzle's JSON mode.
  auditResults: text("audit_results", { mode: "json" }).$type<Record<string, any> | null>(),
  errorMessage: text("error_message"),
});

// === BASE SCHEMAS ===
export const insertFileJobSchema = createInsertSchema(fileJobs).omit({ 
  id: true, 
  uploadedAt: true,
  completedAt: true 
});

// === EXPLICIT API CONTRACT TYPES ===

// Job status type
export type JobStatus = "pending" | "queued" | "processing" | "complete" | "failed";

// File types supported
export type FileType = "pdf" | "jpg" | "png" | "docx" | "pptx";

// Individual audit check result
export interface AuditCheck {
  name: string;
  passed: boolean;
  message: string;
  autoFixed: boolean;
  details?: string;
  cmykVerified?: boolean;
  severity?: string;
}

export interface ResizeAudit {
  originalDimensions: string;
  targetDimensions: string;
  scalePercentage: number;
  cropLossPercent: number;
  aiUpscaled: boolean;
  aspectRatioWarning: boolean;
  falseMargins: boolean;
}

export interface JobAudit {
  originalDpi: number;
  finalDpi: number;
  aiEnhanced: boolean;
  inkSavingsPercent: number;
  safetyStatus: 'SAFE' | 'CRITICAL';
  lensesDetected: boolean;
  lensesFlattened: boolean;
  supersampled: boolean;
  originalTic: number;
  finalTic: number;
}

// Complete audit results
export interface AuditResults {
  checks: AuditCheck[];
  overallPassed: boolean;
  fixesApplied: number;
  complianceReport: string;
  proofPath?: string;
  proofPaths?: string[];
  proofPageCount?: number;
  proofIsBlank?: boolean;
  comparisonPath?: string;
  healthReportPath?: string;
  artworkSize?: {
    width_mm: number;
    height_mm: number;
    has_bleed: boolean;
    bleed_mm: { top: number; bottom: number; left: number; right: number };
    document_width_mm: number;
    document_height_mm: number;
  };
  savedBleedOptions?: Record<string, any>;
  originalDpi?: number;
  showLowDpiWarning?: boolean;
  aiEnhanced?: boolean;
  bleedProofPath?: string;
  bleedVariants?: {
    bgExtract?: string;
    stretch?: string;
    mirror?: string;
    replicate?: string;
    upscale?: string;
    ai_outpaint?: string;
  };
  recommendedBleedMethod?: "bgExtract" | "stretch" | "mirror" | "replicate" | "upscale" | "ai_outpaint";
  selectedBleedMethod?: "bgExtract" | "stretch" | "mirror" | "replicate" | "upscale" | "ai_outpaint" | "auto";
  rightSafety?: "CRITICAL" | "SAFE";
  criticalSafeZone?: boolean;
  preBleedPath?: string;
  aiEnhancements?: {
    denoise?: { enabled: boolean; result: any };
    sharpen_logos?: { enabled: boolean; result: any };
    spell_check?: { enabled: boolean; result: any };
    tac_limit?: { enabled: boolean; result: any };
    trapping?: { enabled: boolean; result: any };
    engagement_score?: { enabled: boolean; result: any };
    background_remove?: { enabled: boolean; result: any };
    text_reconstruct?: { enabled: boolean; result: any };
    spot_uv_mapper?: { enabled: boolean; result: any };
    expand_background?: { enabled: boolean; result: any };
    identify_fonts?: { enabled: boolean; result: any };
    test_design_style?: { enabled: boolean; result: any };
  };
  /** Optional Text Clear-up (OCR→edit→overlay). Absent/unused = no pipeline change. */
  textClearup?: {
    offer?: boolean;
    detected?: boolean;
    reason?: string;
    applied?: boolean;
    blocks?: Array<{
      id: string;
      text: string;
      bbox: number[];
      color_hex?: string;
      align?: string;
      bold?: boolean;
      include?: boolean;
    }>;
  };
  compiledPdfPath?: string;
  compileTaskId?: string;
  compiledStrategy?: string;
  compileAuditReport?: {
    geometry?: { action_taken: string };
    typography?: { action_taken: string };
    color_and_ink?: { action_taken: string };
    resolution_and_lenses?: { action_taken: string };
  };
  jobAudit?: JobAudit;
  /** Present when Shrink & Re-Bleed auto-heal fired during bleed generation (image path). */
  autoHealEvent?: {
    applied: boolean;
    shrinkPxPerSide?: number;
    bleedPxBase?: number;
    bleedPxEffective?: number;
    bleedStrategy?: string;
    safeZoneWarnings?: number;
  } | null;
}

/** Canonical bleed strategy IDs — keep in sync with `compile_press_pdf.py` / `smart_bleed.py`. */
export const BLEED_STRATEGY_IDS = [
  "bgExtract",
  "stretch",
  "mirror",
  "replicate",
  "upscale",
  "ai_outpaint",
] as const;

export type BleedStrategyId = (typeof BLEED_STRATEGY_IDS)[number];

/** Query param values for bleed-preview (`?strategy=`); includes `auto`. */
export const BLEED_STRATEGY_QUERY_VALUES = ["auto", ...BLEED_STRATEGY_IDS] as const;

/** Body `method` for POST `/api/jobs/:id/select-bleed-method`; includes `auto`. */
export const BLEED_METHOD_POST_VALUES = [...BLEED_STRATEGY_IDS, "auto"] as const;

// Base types
export type FileJob = typeof fileJobs.$inferSelect;
export type InsertFileJob = z.infer<typeof insertFileJobSchema>;

// Request types
export interface CreateFileJobRequest {
  filename: string;
  originalPath: string;
  fileSize: number;
  fileType: FileType;
}

export interface UpdateFileJobRequest {
  status?: JobStatus;
  correctedPath?: string;
  completedAt?: Date;
  auditResults?: AuditResults;
  errorMessage?: string;
}

// Response types
export interface FileJobResponse extends Omit<FileJob, 'auditResults'> {
  auditResults: AuditResults | null;
}

export type FileJobsListResponse = FileJobResponse[];

// Bleed adjustment options
export interface BleedOptions {
  targetWidth: number | null;
  targetHeight: number | null;
  defaultBleedSize: number;
  adjustableBleedSize: number;
  colorProfile: "cmyk" | "rgb" | "auto";
  outputType: "print" | "digital";
  extendSolidColors: boolean;
  enableGradientFade: boolean;
  addBorder: boolean;
  separateLayers: boolean;
  useClippingMasks: boolean;
  sampleEdgeColors: boolean;
  increaseBleedMargins: boolean;
  resizeArtwork: boolean;
  adjustTrimLines: boolean;
  useTemplates: boolean;
  consultPrinters: boolean;
  createMockups: boolean;
  autoSafeZoneFix: boolean;
  enableLayoutBalancing: boolean;
  enableCompositionCenter: boolean;
  enableSmartDownscale: boolean;
  enableMarginNormalization: boolean;
  enableToleranceSimulation: boolean;
  enableSpineShiftDetection: boolean;
  enableCreepCompensation: boolean;
  enableGutterCollisionCheck: boolean;
  enableWhiteEdgeRisk: boolean;
  enablePdfxCompliance: boolean;
}

export const defaultBleedOptions: BleedOptions = {
  targetWidth: null,
  targetHeight: null,
  defaultBleedSize: 5,
  adjustableBleedSize: 5,
  colorProfile: "cmyk",
  outputType: "print",
  extendSolidColors: true,
  enableGradientFade: false,
  addBorder: false,
  separateLayers: false,
  useClippingMasks: false,
  sampleEdgeColors: true,
  increaseBleedMargins: false,
  resizeArtwork: false,
  adjustTrimLines: false,
  useTemplates: false,
  consultPrinters: false,
  createMockups: false,
  autoSafeZoneFix: true,
  enableLayoutBalancing: true,
  enableCompositionCenter: true,
  enableSmartDownscale: true,
  enableMarginNormalization: true,
  enableToleranceSimulation: true,
  enableSpineShiftDetection: true,
  enableCreepCompensation: true,
  enableGutterCollisionCheck: true,
  enableWhiteEdgeRisk: true,
  enablePdfxCompliance: true,
};

// File upload response
export interface FileUploadResponse {
  jobId: number;
  filename: string;
  status: JobStatus;
}

// Download response (handled as file stream, not JSON)
export interface FileDownloadParams {
  jobId: number;
  type: 'original' | 'corrected' | 'report';
}
