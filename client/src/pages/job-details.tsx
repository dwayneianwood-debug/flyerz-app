import { Layout } from "@/components/layout";
import { useJob, useProcessJob } from "@/hooks/use-jobs";
import { useRoute, useSearch, Link } from "wouter";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import { 
  ArrowLeft, ArrowRight, FileText, Download, Wand2, 
  CheckCircle2, XCircle, Loader2, AlertCircle, FileCheck, ShieldCheck,
  Eye, AlertTriangle, Heart, Scissors, Maximize, Sparkles, PartyPopper,
  ChevronRight, ChevronDown, Send, Mail, RotateCcw, Zap, Droplets, Shield,
  Target, Eraser, Type, Expand, ScanSearch, Palette, Wrench
} from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { format } from "date-fns";
import { motion, AnimatePresence } from "framer-motion";
import { Progress } from "@/components/ui/progress";
import { useEffect, useState, useRef, useMemo, memo } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "@/components/ui/collapsible";
import { apiRequest, queryClient } from "@/lib/queryClient";
import { useToast } from "@/hooks/use-toast";
import { blobDownload } from "@/lib/blob-download";
import { useBeta } from "@/lib/beta-flag";
import {
  isSafeZoneLayoutError,
  handleSafeZoneLayoutProcessingError,
} from "@/lib/safe-zone-error";
import { BLEED_STRATEGY_IDS } from "@shared/schema";

/** Unwrap SQLite / double-JSON string blobs (same idea as server `unfoldJsonValue`). */
function unfoldStringJson(val: unknown, maxDepth = 8): unknown {
  let cur: unknown = val;
  for (let i = 0; i < maxDepth && typeof cur === "string"; i++) {
    const s = cur.trim();
    if (!s || (s[0] !== "{" && s[0] !== "[")) break;
    try {
      cur = JSON.parse(s);
    } catch {
      break;
    }
  }
  return cur;
}

function bleedOptsObjectFromAudit(audit: Record<string, any> | null | undefined): Record<string, any> {
  if (!audit) return {};
  const raw = audit.savedBleedOptions ?? audit.bleedOptions;
  const u = unfoldStringJson(raw);
  if (u && typeof u === "object" && !Array.isArray(u)) return u as Record<string, any>;
  return {};
}

/** Trim size (mm) from saved audit bleed options; A5 only when unset (matches server fallbacks). */
function trimMmFromAudit(audit: Record<string, any> | null | undefined): { trimW: number; trimH: number } {
  const o = bleedOptsObjectFromAudit(audit);
  const tw = Number(o.targetWidth);
  const th = Number(o.targetHeight);
  if (Number.isFinite(tw) && tw > 0 && Number.isFinite(th) && th > 0) {
    return { trimW: tw, trimH: th };
  }
  return { trimW: 148, trimH: 210 };
}

function LazyCollapsibleContent({ isOpen, betaMode, resetKey, children }: { isOpen: boolean; betaMode: boolean; resetKey?: number; children: React.ReactNode }) {
  const [hasOpened, setHasOpened] = useState(!betaMode || isOpen);
  useEffect(() => { if (isOpen && !hasOpened) setHasOpened(true); }, [isOpen, hasOpened]);
  useEffect(() => { if (betaMode) setHasOpened(false); }, [resetKey, betaMode]);
  return <CollapsibleContent>{hasOpened ? children : null}</CollapsibleContent>;
}

interface BleedPreviewPage {
  page: number;
  url: string;
  downloadUrl: string;
  totalSize_mm: [number, number];
  trimSize_mm: [number, number];
  bleed_mm: number;
}

interface BleedPreviewData {
  previewUrls: BleedPreviewPage[];
  pageCount: number;
}

const PHASE_LABELS = [
  { num: 1, label: "Upload & Fix", icon: Wand2 },
  { num: 2, label: "Review Artwork", icon: Eye },
  { num: 3, label: "Download", icon: Download },
];

const BLEED_METHOD_LABELS = {
  bgExtract: {
    label: "Background Extract",
    description: "Extends only the background colour, ideal for artwork with text near the edge.",
  },
  stretch: {
    label: "Pixel-Drift Stretch",
    description: "Projects edge pixels outward with gentle drift, best for complex images.",
  },
  mirror: {
    label: "Mirror + Blend",
    description: "Mirrors the edge and cross-fades the seam, great for busy textures.",
  },
  replicate: {
    label: "Edge Replication",
    description: "Repeats the outermost pixel row, cleanest for solid colours.",
  },
  upscale: {
    label: "Upscale",
    description: "Smoothly scales entire artwork to include bleed — perfectly smooth boundaries.",
  },
  ai_outpaint: {
    label: "AI Outpaint",
    description:
      "Fast proxy inpainting extends bleed colors softly; your 300 DPI artwork stays pixel-perfect in the center.",
  },
} as const;

export default function JobDetails() {
  const [, params] = useRoute("/job/:id");
  const id = params?.id ? parseInt(params.id, 10) : 0;
  const betaMode = useBeta();
  
  const { data: job, isLoading, error } = useJob(id);
  const processJob = useProcessJob();
  
  const [progress, setProgress] = useState(0);
  const [proofChecked, setProofChecked] = useState(false);
  const [hasDownloaded, setHasDownloaded] = useState(false);
  const [shareEmail, setShareEmail] = useState('');
  const [isSharing, setIsSharing] = useState(false);
  const [shareComplete, setShareComplete] = useState(false);
  const { toast } = useToast();
  const [proofLoadError, setProofLoadError] = useState(false);
  const [proofPage, setProofPage] = useState(0);
  const [proofRefreshKey, setProofRefreshKey] = useState(() => Date.now());
  const [comparisonLoadError, setComparisonLoadError] = useState(false);
  const [comparisonRefreshKey, setComparisonRefreshKey] = useState(() => Date.now());
  const [bleedPreview, setBleedPreview] = useState<BleedPreviewData | null>(null);
  const [bleedPreviewLoading, setBleedPreviewLoading] = useState(false);
  const [bleedPreviewError, setBleedPreviewError] = useState<string | null>(null);
  const [bleedPreviewPage, setBleedPreviewPage] = useState(0);
  const [bleedChecked, setBleedChecked] = useState(false);
  const [comparisonChecked, setComparisonChecked] = useState(false);
  const [phaseOverride, setPhaseOverride] = useState<number | null>(null);
  const [selectedBleedMethod, setSelectedBleedMethod] = useState<string>("auto");
  const [bleedMethodLoading, setBleedMethodLoading] = useState(false);
  const [compileTaskId, setCompileTaskId] = useState<string | null>(null);
  const [compileState, setCompileState] = useState<string | null>(null);
  const [compileMessage, setCompileMessage] = useState<string>("");
  const [compileDownloadUrl, setCompileDownloadUrl] = useState<string | null>(null);
  const [compileError, setCompileError] = useState<string | null>(null);
  const [downloadingFile, setDownloadingFile] = useState<string | null>(null);
  const [preCompileState, setPreCompileState] = useState<string | null>(null);
  const [preCompileMessage, setPreCompileMessage] = useState<string>("");
  const [phase3Confirmed, setPhase3Confirmed] = useState(false);
  const [isFastTrack, setIsFastTrack] = useState(false);
  const [fastTrackTriggered, setFastTrackTriggered] = useState(false);
  const [autoFixApplied, setAutoFixApplied] = useState(false);
  const [aiDenoise, setAiDenoise] = useState(false);
  const [aiSharpenLogos, setAiSharpenLogos] = useState(false);
  const [aiSpellCheck, setAiSpellCheck] = useState(false);
  const [aiTacLimit, setAiTacLimit] = useState(false);
  const [aiTrapping, setAiTrapping] = useState(false);
  const [aiEngagementScore, setAiEngagementScore] = useState(false);
  const [aiBackgroundRemove, setAiBackgroundRemove] = useState(false);
  const [aiTextReconstruct, setAiTextReconstruct] = useState(false);

  const [aiExpandBackground, setAiExpandBackground] = useState(false);
  const [aiIdentifyFonts, setAiIdentifyFonts] = useState(false);
  const [aiTestDesignStyle, setAiTestDesignStyle] = useState(false);
  const [autoShifterEnabled, setAutoShifterEnabled] = useState(false);
  const [aiEnhanceLoading, setAiEnhanceLoading] = useState<string | null>(null);
  const [aiEnhanceMessages, setAiEnhanceMessages] = useState<Record<string, string>>({});
  const [prepressOverlay, setPrepressOverlay] = useState<{ type: string; label: string } | null>(null);
  const [prepressSpinnerActive, setPrepressSpinnerActive] = useState(false);
  const [guillotineOpen, setGuillotineOpen] = useState(false);
  const [reviewTab, setReviewTab] = useState<"review" | "tools">("review");
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    preflight: false,
    outpaint: false,
    prepress: false,
    aiEnhancements: false,
    marketing: false,
  });

  // Optional Text Clear-up (only shown when soft text detected after bleed select)
  type TextClearupBlock = {
    id: string;
    text: string;
    bbox: number[];
    color_hex?: string;
    align?: string;
    bold?: boolean;
    include?: boolean;
  };
  const [textClearupOffer, setTextClearupOffer] = useState(false);
  const [textClearupReason, setTextClearupReason] = useState("");
  const [textClearupPhase, setTextClearupPhase] = useState<"idle" | "detecting" | "offered" | "ocr" | "editing" | "applying" | "done" | "skipped">("idle");
  const [textClearupBlocks, setTextClearupBlocks] = useState<TextClearupBlock[]>([]);
  const [textClearupBusy, setTextClearupBusy] = useState(false);
  const [textClearupError, setTextClearupError] = useState<string | null>(null);

  const searchString = useSearch();
  const urlParams = new URLSearchParams(searchString);
  const isDevMode = urlParams.get("dev") === "true";
  const isAdminMode = urlParams.get("admin") === "true";

  const toggleSection = (key: string, open?: boolean) => {
    setOpenSections(prev => ({ ...prev, [key]: open !== undefined ? open : !prev[key] }));
  };

  const handleBlobDownload = async (url: string, filename: string, fileKey: string) => {
    if (downloadingFile) return;
    setDownloadingFile(fileKey);
    const success = await blobDownload(url, filename, (errMsg) => {
      toast({
        title: "Download Failed",
        description: errMsg,
        variant: "destructive",
      });
    });
    if (success && (fileKey === "press-ready" || fileKey === "bundle")) {
      setHasDownloaded(true);
    }
    setDownloadingFile(null);
  };


  useEffect(() => {
    if (job && (job.status as string) === "queued") {
      const jobId = job.id;
      setProgress(0);
      const pollQueue = setInterval(async () => {
        try {
          const res = await fetch(`/api/jobs/${jobId}/queue-position`);
          const data = await res.json();
          if (data.queued && data.position > 0) {
            window.dispatchEvent(new CustomEvent("glitchy:queue-update", {
              detail: { position: data.position }
            }));
          }
        } catch {}
      }, 3000);
      window.dispatchEvent(new CustomEvent("glitchy:queue-update", {
        detail: { position: 1 }
      }));
      return () => clearInterval(pollQueue);
    } else if (job?.status === "processing") {
      window.dispatchEvent(new CustomEvent("glitchy:queue-dequeued"));
      const interval = setInterval(() => {
        setProgress(p => Math.min(p + Math.random() * 15, 95));
      }, 500);
      return () => clearInterval(interval);
    } else if (job?.status === "complete" || job?.status === "failed") {
      setProgress(100);
    } else {
      setProgress(0);
    }
  }, [job?.status]);

  useEffect(() => {
    if (job?.status === "processing") {
      window.dispatchEvent(
        new CustomEvent("glitchy:audit-sync", { detail: { overallPassed: false } }),
      );
    }
  }, [job?.status]);

  /** Background process failures (batch/async): Glitchy only — no duplicate toast. */
  useEffect(() => {
    if (!job || job.status !== "failed") return;
    const payload = job.errorMessage ?? job.auditResults?.checks?.find(
      (c) => c.name === "Safe Zone Layout",
    )?.message;
    if (!isSafeZoneLayoutError(payload)) return;
    if (layoutGlitchyDispatchedForJobRef.current === job.id) return;
    layoutGlitchyDispatchedForJobRef.current = job.id;
    handleSafeZoneLayoutProcessingError(payload);
  }, [job?.id, job?.status, job?.errorMessage, job?.auditResults?.checks]);

  useEffect(() => {
    if (job?.status !== "complete" || !job.auditResults) return;
    window.dispatchEvent(
      new CustomEvent("glitchy:audit-sync", {
        detail: { overallPassed: job.auditResults.overallPassed === true },
      }),
    );
  }, [job?.status, job?.auditResults?.overallPassed, job?.id]);

  useEffect(() => {
    setProgress(0);
    setProofChecked(false);
    setHasDownloaded(false);
    setShareEmail('');
    setIsSharing(false);
    setShareComplete(false);
    setProofLoadError(false);
    setProofPage(0);
    setProofRefreshKey(Date.now());
    setComparisonLoadError(false);
    setComparisonRefreshKey(Date.now());
    setBleedPreview(null);
    setBleedPreviewLoading(false);
    setBleedPreviewError(null);
    setBleedPreviewPage(0);
    setBleedChecked(false);
    setComparisonChecked(false);
    setPhaseOverride(null);
    setSelectedBleedMethod("auto");
    autoSelectTriggeredRef.current = false;
    setBleedMethodLoading(false);
    setCompileTaskId(null);
    setCompileState(null);
    setCompileMessage("");
    setCompileDownloadUrl(null);
    setCompileError(null);
    setDownloadingFile(null);
    setPreCompileState(null);
    setPreCompileMessage("");
    setPhase3Confirmed(false);
    setIsFastTrack(false);
    setFastTrackTriggered(false);
    setAutoFixApplied(false);
    setAiDenoise(false);
    setAiSharpenLogos(false);
    setAiSpellCheck(false);
    setAiTacLimit(false);
    setAiTrapping(false);
    setAiEngagementScore(false);
    setAiBackgroundRemove(false);
    setAiTextReconstruct(false);

    setAiExpandBackground(false);
    setAiIdentifyFonts(false);
    setAiTestDesignStyle(false);
    setAutoShifterEnabled(false);
    setAiEnhanceLoading(null);
    setAiEnhanceMessages({});
    setReviewTab("review");

    layoutGlitchyDispatchedForJobRef.current = null;
    window.dispatchEvent(new CustomEvent("glitchy:job-reset"));
  }, [id]);

  useEffect(() => {
    if (job?.status === "complete" && job?.correctedPath && !bleedPreview && !bleedPreviewLoading) {
      loadBleedPreview();
    }
  }, [job?.status, job?.correctedPath]);

  useEffect(() => {
    if (job?.auditResults?.selectedBleedMethod) {
      setSelectedBleedMethod(job.auditResults.selectedBleedMethod);
    }
  }, [job?.auditResults?.selectedBleedMethod]);

  useEffect(() => {
    const ai = (job?.auditResults as any)?.aiEnhancements;
    if (ai) {
      if (ai.denoise?.enabled) setAiDenoise(true);
      if (ai.sharpen_logos?.enabled) setAiSharpenLogos(true);
      if (ai.spell_check?.enabled) setAiSpellCheck(true);
      if (ai.tac_limit?.enabled) setAiTacLimit(true);
      if (ai.trapping?.enabled) setAiTrapping(true);
      if (ai.engagement_score?.enabled) setAiEngagementScore(true);
      if (ai.background_remove?.enabled) setAiBackgroundRemove(true);
      if (ai.text_reconstruct?.enabled) setAiTextReconstruct(true);

      if (ai.expand_background?.enabled) setAiExpandBackground(true);
      if (ai.identify_fonts?.enabled) setAiIdentifyFonts(true);
      if (ai.test_design_style?.enabled) setAiTestDesignStyle(true);
      const msgs: Record<string, string> = {};
      const keys = ["denoise", "sharpen_logos", "spell_check", "tac_limit", "trapping", "engagement_score", "background_remove", "text_reconstruct", "expand_background", "identify_fonts", "test_design_style"];
      for (const k of keys) {
        if (ai[k]?.result?.message) msgs[k] = ai[k].result.message;
      }
      if (Object.keys(msgs).length > 0) setAiEnhanceMessages(msgs);
    }
  }, [job?.auditResults]);

  const autoSelectTriggeredRef = useRef(false);
  const layoutGlitchyDispatchedForJobRef = useRef<number | null>(null);
  useEffect(() => {
    if (!job || job.status !== "complete" || autoSelectTriggeredRef.current) return;
    if (selectedBleedMethod !== "auto") return;
    const variants = job.auditResults?.bleedVariants;
    const hasVariants = variants && Object.keys(variants).length > 0;
    if (hasVariants) return;
    const recommended = job.auditResults?.recommendedBleedMethod || "mirror";
    autoSelectTriggeredRef.current = true;
    handleBleedMethodSelect(recommended);
  }, [job?.id, job?.status, job?.auditResults?.bleedVariants, selectedBleedMethod]);

  const noneCountRef = useRef(0);

  useEffect(() => {
    if (!job || job.status !== "complete") return;
    if (selectedBleedMethod === "auto") return;
    let cancelled = false;
    noneCountRef.current = 0;
    const MAX_NONE_POLLS = 8;
    const poll = async () => {
      try {
        const res = await fetch(`/api/jobs/${job.id}/precompile-status?strategy=${encodeURIComponent(selectedBleedMethod)}`);
        const data = await res.json();
        if (cancelled) return;
        if (data.state === "ready") {
          setPreCompileState("ready");
          setPreCompileMessage(data.message || "");
          setCompileState("COMPLETE");
          setCompileDownloadUrl(`/api/jobs/${job.id}/download-bundle?strategy=${encodeURIComponent(selectedBleedMethod)}`);
          queryClient.invalidateQueries({ queryKey: ["job", job.id] });
          noneCountRef.current = 0;
          cancelled = true;
        } else if (data.state === "failed") {
          setPreCompileState("failed");
          setPreCompileMessage(data.message || "");
          setCompileState("FAILURE");
          setCompileError(data.error || data.message || "Pre-compilation failed");
          window.dispatchEvent(new CustomEvent("glitchy:compile-error", { detail: { message: data.error || "Compilation failed" } }));
          noneCountRef.current = 0;
          cancelled = true;
        } else if (data.state === "none") {
          noneCountRef.current++;
          if (noneCountRef.current >= MAX_NONE_POLLS) {
            setPreCompileState("ready");
            setPreCompileMessage("Artwork ready for download.");
            setCompileState("COMPLETE");
            setCompileDownloadUrl(`/api/jobs/${job.id}/download-bundle?strategy=${encodeURIComponent(selectedBleedMethod)}`);
            noneCountRef.current = 0;
            cancelled = true;
          }
        } else if (data.state === "compiling") {
          setPreCompileState("compiling");
          setPreCompileMessage(data.message || "");
          noneCountRef.current = 0;
        }
      } catch {}
    };
    poll();
    const interval = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [job?.id, job?.status, selectedBleedMethod]);

  useEffect(() => {
    if (!job?.auditResults?.compiledPdfPath || compileTaskId) return;
    if (selectedBleedMethod === "auto") return;
    if (preCompileState !== "ready") return;

    const dlUrl = `/api/jobs/${job.id}/download-bundle?strategy=${encodeURIComponent(selectedBleedMethod)}`;
    setCompileState("COMPLETE");
    setCompileDownloadUrl(dlUrl);
  }, [job?.auditResults?.compiledPdfPath, selectedBleedMethod, preCompileState]);

  useEffect(() => {
    if (!compileTaskId || !job) return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${job.id}/compile-status/${compileTaskId}`);
        const data = await res.json();
        setCompileState(data.state);
        const msg = data.message || "";
        setCompileMessage(msg);
        if (msg && (msg.includes("Converting") || msg.includes("CMYK") || msg.includes("Neutralizing") || msg.includes("Trapping") || msg.includes("trap"))) {
          window.dispatchEvent(new CustomEvent("glitchy:ink-stain", { detail: { message: "Mixing the perfect ink colors..." } }));
        }
        if (data.state === "COMPLETE") {
          setCompileDownloadUrl(data.downloadUrl || null);
          setCompileState("COMPLETE");
          clearInterval(interval);
          await queryClient.invalidateQueries({ queryKey: ["job", job.id] });
        } else if (data.state === "FAILURE") {
          const errMsg = data.error || data.message || "Compilation failed";
          setCompileError(errMsg);
          clearInterval(interval);
          window.dispatchEvent(new CustomEvent("glitchy:compile-error", { detail: { message: errMsg } }));
        }
      } catch {
        clearInterval(interval);
        setCompileError("Lost connection to server");
        window.dispatchEvent(new CustomEvent("glitchy:compile-error", { detail: { message: "Lost connection to server" } }));
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [compileTaskId, job?.id]);

  const handleCompilePressReady = async (strategyOverride?: string): Promise<boolean> => {
    if (!job) return false;
    const strategy = strategyOverride || selectedBleedMethod;
    setCompileState("PENDING");
    setCompileMessage("Starting compilation...");
    setCompileDownloadUrl(null);
    setCompileError(null);
    const auditForGlitchy = job.auditResults?.jobAudit;
    window.dispatchEvent(new CustomEvent("glitchy:compile-start", { detail: {
      message: "Compiling your press-ready PDF...",
      lensesDetected: auditForGlitchy?.lensesDetected ?? false,
      lensesFlattened: auditForGlitchy?.lensesFlattened ?? false,
      supersampled: auditForGlitchy?.supersampled ?? false,
      aiEnhanced: auditForGlitchy?.aiEnhanced ?? false,
      originalTic: auditForGlitchy?.originalTic ?? null,
      finalTic: auditForGlitchy?.finalTic ?? null,
    } }));
    try {
      const auditResults = job.auditResults as any;
      const oo = bleedOptsObjectFromAudit(auditResults);
      const { trimW, trimH } = trimMmFromAudit(auditResults);
      const colorProfile = oo.colorProfile || "cmyk";

      const savedCrop = oo;
      const cropPayload = (savedCrop?.cropX != null && savedCrop?.cropWidth > 0) ? {
        cropX: savedCrop.cropX,
        cropY: savedCrop.cropY,
        cropWidth: savedCrop.cropWidth,
        cropHeight: savedCrop.cropHeight,
      } : undefined;
      console.log(`[COMPILE] Proceed to Download handoff: hasCrop=${!!cropPayload}, trimSize=${trimW}×${trimH}mm, strategy=${strategy}${cropPayload ? `, crop=(${cropPayload.cropX},${cropPayload.cropY}) ${cropPayload.cropWidth}×${cropPayload.cropHeight}` : ''}`);

      const res = await apiRequest("POST", `/api/jobs/${job.id}/compile-print-pdf`, {
        selectedStrategy: strategy,
        exportPreferences: {
          colorSpace: colorProfile,
          trimWidth: trimW,
          trimHeight: trimH,
        },
        cropData: cropPayload,
        targetSize: { width: trimW, height: trimH },
        autoShifter: autoShifterEnabled,
      });
      const data = await res.json();
      setCompileTaskId(data.taskId);
      setCompileState("PENDING");
      setCompileMessage("Waiting in queue...");
      return true;
    } catch (err: any) {
      const errMsg = err.message || "Failed to start compilation";
      setCompileState("FAILURE");
      setCompileError(errMsg);
      window.dispatchEvent(new CustomEvent("glitchy:compile-error", { detail: { message: errMsg } }));
      return false;
    }
  };

  const PREPRESS_OVERLAY_LABELS: Record<string, string> = {
    tac_limit: "Ink Profile Updated",
    trapping: "Trap Logic Applied",
  };

  const handleAiEnhancementToggle = async (enhancement: string, enabled: boolean) => {
    if (!job || aiEnhanceLoading) return;

    const setters: Record<string, (v: boolean) => void> = {
      denoise: setAiDenoise,
      sharpen_logos: setAiSharpenLogos,
      spell_check: setAiSpellCheck,
      tac_limit: setAiTacLimit,
      trapping: setAiTrapping,
      engagement_score: setAiEngagementScore,
      background_remove: setAiBackgroundRemove,
      text_reconstruct: setAiTextReconstruct,

      expand_background: setAiExpandBackground,
      identify_fonts: setAiIdentifyFonts,
      test_design_style: setAiTestDesignStyle,
    };

    const isPrepressToggle = enhancement === "tac_limit" || enhancement === "trapping";

    setters[enhancement]?.(enabled);
    setAiEnhanceLoading(enhancement);

    if (isPrepressToggle && enabled) {
      setPrepressSpinnerActive(true);
      setPrepressOverlay({ type: "loading", label: PREPRESS_OVERLAY_LABELS[enhancement] || enhancement });
    }

    try {
      const res = await apiRequest("POST", `/api/jobs/${job.id}/ai-enhance`, {
        enhancement,
        enabled,
        options: {},
      });
      const data = await res.json();

      if (data.stub) {
        setAiEnhanceMessages(prev => ({
          ...prev,
          [enhancement]: data.message || "Enhancement stub active — external API not yet connected.",
        }));
      } else {
        setAiEnhanceMessages(prev => ({
          ...prev,
          [enhancement]: enabled ? "Enhancement applied." : "Reverted to original.",
        }));
      }

      await queryClient.invalidateQueries({ queryKey: ["job", job.id] });
      setProofRefreshKey(Date.now());
      setComparisonRefreshKey(Date.now());

      if (isPrepressToggle && enabled) {
        const successLabel = PREPRESS_OVERLAY_LABELS[enhancement] || "Applied";
        await new Promise(resolve => setTimeout(resolve, 1500));
        setPrepressOverlay({ type: "success", label: successLabel });
        setTimeout(() => {
          setPrepressOverlay(null);
          setPrepressSpinnerActive(false);
        }, 1200);
      }
    } catch (err: any) {
      setters[enhancement]?.(false);
      setPrepressOverlay(null);
      setPrepressSpinnerActive(false);
      toast({
        title: "Enhancement Failed",
        description: err.message || `Could not apply ${enhancement}`,
        variant: "destructive",
      });
    } finally {
      setAiEnhanceLoading(null);
      setPrepressSpinnerActive(false);
      setPrepressOverlay(null);
    }
  };

  const PREMIUM_FEATURES: Record<string, string> = {
    denoise: "Clean up photo grain",
    sharpen_logos: "Sharpen blurry logos",
    spell_check: "Check Spelling (SA Languages)",
    background_remove: "Clean Background",
    expand_background: "Expand Background",
    identify_fonts: "Identify Fonts",
    test_design_style: "Eye-Catching Score",
  };

  const handlePremiumToggle = (enhancement: string, checked: boolean) => {
    handleAiEnhancementToggle(enhancement, checked);
  };

  const PremiumBadge = () => (
    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider rounded-full bg-gradient-to-r from-amber-100 to-yellow-100 dark:from-amber-500/20 dark:to-yellow-500/20 text-amber-700 dark:text-amber-300 border border-amber-300/40 dark:border-amber-500/30" data-testid="badge-premium">
      <span>👑</span>
      <span>Premium</span>
    </span>
  );

  const handleFastTrack = async () => {
    if (!job || fastTrackTriggered) return;
    setFastTrackTriggered(true);
    setIsFastTrack(true);

    const recommended = job.auditResults?.recommendedBleedMethod || "mirror";
    console.log(`[FAST-TRACK] One-Click Approve triggered: job=${job.id}, strategy=${recommended}`);

    setSelectedBleedMethod(recommended);
    setProofChecked(true);
    setBleedChecked(true);
    setComparisonChecked(true);

    try {
      const selectRes = await fetch(`/api/jobs/${job.id}/select-bleed-method`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method: recommended }),
      });
      if (!selectRes.ok) throw new Error("Failed to set bleed method");

      const compileOk = await handleCompilePressReady(recommended);

      if (compileOk) {
        setPhase3Confirmed(true);
        setPhaseOverride(null);
      } else {
        setFastTrackTriggered(false);
        setIsFastTrack(false);
      }
    } catch (err) {
      console.error("[FAST-TRACK] Failed:", err);
      setFastTrackTriggered(false);
      setIsFastTrack(false);
    }
  };

  const handleBleedMethodSelect = async (method: string, forceRecompile = false) => {
    if (!job || bleedMethodLoading) return;
    if (!forceRecompile && method === selectedBleedMethod) return;

    console.log(`TRACER: [FE-A] handleBleedMethodSelect: method="${method}" previousSelected="${selectedBleedMethod}" forceRecompile=${forceRecompile}`);
    setBleedMethodLoading(true);
    setBleedPreviewLoading(true);

    const methodLabel = BLEED_METHOD_LABELS[method as keyof typeof BLEED_METHOD_LABELS]?.label || method;
    window.dispatchEvent(new CustomEvent("glitchy:bleed-switch", {
      detail: { method, message: `Switching bleed to ${methodLabel}...` }
    }));

    try {
      const res = await fetch(`/api/jobs/${job.id}/select-bleed-method`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method }),
      });

      const data = await res.json();

      if (!res.ok || !data.success) {
        throw new Error(data.message || "Backend rejected the bleed strategy change");
      }

      if (data.strategy !== method) {
        throw new Error(`Strategy mismatch: requested '${method}' but backend confirmed '${data.strategy}'`);
      }

      const serverTs = data.timestamp || Date.now();

      setSelectedBleedMethod(method);
      setCompileState(null);
      setCompileDownloadUrl(null);
      setCompileError(null);
      setCompileTaskId(null);
      setPreCompileState("compiling");
      setPreCompileMessage("Pre-compiling in background...");
      setPhase3Confirmed(false);
      queryClient.invalidateQueries({ queryKey: ["job", job.id] });

      await loadBleedPreview(method);

      setProofRefreshKey(serverTs);
      setProofLoadError(false);
      setComparisonRefreshKey(serverTs);
      setComparisonLoadError(false);

      toast({
        title: "Bleed method updated",
        description: `Switched to ${methodLabel}`,
      });

      window.dispatchEvent(new CustomEvent("glitchy:bleed-switch-done", {
        detail: { method, message: `Bleed updated to ${methodLabel}` }
      }));

      // Optional soft-text detection — never blocks bleed/compile if it fails.
      // Skip re-detect when only refreshing compile after text clear-up apply.
      if (!forceRecompile) {
        setTextClearupPhase("detecting");
        setTextClearupOffer(false);
        setTextClearupError(null);
        setTextClearupBlocks([]);
        try {
          const detRes = await fetch(`/api/jobs/${job.id}/text-clearup/detect`, { method: "POST" });
          const det = await detRes.json();
          if (det.offer_clearup) {
            setTextClearupOffer(true);
            setTextClearupReason(det.reason || det.message || "Soft text detected");
            setTextClearupPhase("offered");
          } else {
            setTextClearupOffer(false);
            setTextClearupPhase("idle");
          }
        } catch {
          setTextClearupOffer(false);
          setTextClearupPhase("idle");
        }
      }

    } catch (err: any) {
      const errMsg = err.message || "An unknown error occurred while switching bleed strategy";
      toast({
        title: "Bleed Strategy Error",
        description: errMsg,
        variant: "destructive",
      });
      window.dispatchEvent(new CustomEvent("glitchy:bleed-switch-done", {
        detail: { error: true, message: errMsg }
      }));
    } finally {
      setBleedMethodLoading(false);
      setBleedPreviewLoading(false);
    }
  };

  const handleTextClearupStartOcr = async () => {
    if (!job || textClearupBusy) return;
    setTextClearupBusy(true);
    setTextClearupError(null);
    setTextClearupPhase("ocr");
    try {
      const res = await fetch(`/api/jobs/${job.id}/text-clearup/ocr`, { method: "POST" });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || "OCR failed");
      }
      setTextClearupBlocks((data.blocks || []).map((b: TextClearupBlock) => ({ ...b, include: b.include !== false })));
      setTextClearupPhase("editing");
      toast({ title: "OCR ready", description: "Check and edit any wrong words before applying." });
    } catch (e: any) {
      setTextClearupError(e.message || "OCR failed");
      setTextClearupPhase("offered");
      toast({ title: "OCR failed", description: e.message || "Try again", variant: "destructive" });
    } finally {
      setTextClearupBusy(false);
    }
  };

  const handleTextClearupSkip = async () => {
    if (!job) return;
    try {
      await fetch(`/api/jobs/${job.id}/text-clearup/skip`, { method: "POST" });
    } catch {}
    setTextClearupOffer(false);
    setTextClearupPhase("skipped");
    setTextClearupBlocks([]);
  };

  const handleTextClearupApply = async () => {
    if (!job || textClearupBusy) return;
    const included = textClearupBlocks.filter((b) => b.include !== false && (b.text || "").trim());
    if (!included.length) {
      toast({ title: "No text selected", description: "Include at least one text block, or skip.", variant: "destructive" });
      return;
    }
    setTextClearupBusy(true);
    setTextClearupError(null);
    setTextClearupPhase("applying");
    try {
      const res = await fetch(`/api/jobs/${job.id}/text-clearup/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ blocks: textClearupBlocks }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || "Apply failed");
      }
      setTextClearupPhase("done");
      setTextClearupOffer(false);
      toast({
        title: "Text clear-up applied",
        description: "Rebuilding print-ready file with sharp text…",
      });
      // Re-run existing bleed compile only — does not change bleed strategy logic
      await handleBleedMethodSelect(selectedBleedMethod, true);
    } catch (e: any) {
      setTextClearupError(e.message || "Apply failed");
      setTextClearupPhase("editing");
      toast({ title: "Apply failed", description: e.message || "Try again", variant: "destructive" });
    } finally {
      setTextClearupBusy(false);
    }
  };

  const loadBleedPreview = async (strategy?: string) => {
    if (!job || !job.correctedPath) return;
    setBleedPreviewLoading(true);
    setBleedPreviewError(null);

    try {
      const strategyParam = strategy && strategy !== "auto" ? `?strategy=${strategy}` : "";
      const res = await fetch(`/api/jobs/${job.id}/bleed-preview${strategyParam}`);
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.message || "Failed to generate bleed preview");
      }
      const data = await res.json();
      setBleedPreview(data);
      setBleedPreviewPage(0);
    } catch (err: any) {
      setBleedPreviewError(err.message);
    } finally {
      setBleedPreviewLoading(false);
    }
  };

  if (isLoading) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[50vh]">
          <Loader2 className="w-10 h-10 animate-spin text-primary" />
        </div>
      </Layout>
    );
  }

  if (error || !job) {
    return (
      <Layout>
        <Card className="p-12 text-center max-w-2xl mx-auto mt-12 bg-destructive/5 border-destructive/20">
          <AlertCircle className="w-16 h-16 text-destructive mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-foreground mb-2">Job Not Found</h2>
          <p className="text-muted-foreground mb-6">The audit job you are looking for doesn't exist or an error occurred.</p>
          <Link href="/">
            <Button className="hover-elevate">Return to Dashboard</Button>
          </Link>
        </Card>
      </Layout>
    );
  }

  const isQueued = (job.status as string) === "queued";
  const isProcessing = job.status === "processing" || processJob.isPending;
  const isPending = job.status === "pending";
  const isComplete = job.status === "complete";
  const hasFailedChecks = job.auditResults?.overallPassed === false;
  const hasProof = isComplete && (job.auditResults?.proofPath || job.correctedPath || job.originalPath);
  const proofIsBlank = job.auditResults?.proofIsBlank;
  const originalDpi = job.auditResults?.originalDpi;
  const showLowDpiWarning = job.auditResults?.showLowDpiWarning;
  const jobAudit = job.auditResults?.jobAudit;
  const hasComparison = isComplete && job.auditResults?.comparisonPath;
  const overallPassed = job.auditResults?.overallPassed === true;
  const allReviewsChecked = bleedChecked && (hasComparison ? comparisonChecked : true) && !prepressSpinnerActive;
  const canDownload = isComplete && overallPassed && (hasProof ? proofChecked : true);
  const hasCorrectedFile = isComplete && overallPassed && job.correctedPath;

  const currentBleedPage = bleedPreview?.previewUrls?.[bleedPreviewPage];

  const isFailed = job.status === "failed";
  const hasUserSelectedBleed = selectedBleedMethod !== "auto";
  const preCompileReady = preCompileState === "ready" || compileState === "COMPLETE";
  const fastTrackEligible = (() => {
    if (!isComplete || !overallPassed || !job.auditResults?.checks) return false;
    const checks = job.auditResults.checks;
    const bleedOk = checks.some(c => (c.name.includes("Bleed") || c.name.includes("bleed")) && c.passed);
    const resOk = checks.some(c => (c.name.includes("Resolution") || c.name.includes("DPI") || c.name.includes("dpi")) && c.passed);
    const colorOk = checks.some(c => (c.name.includes("Color") || c.name.includes("CMYK") || c.name.includes("colour")) && c.passed);
    return bleedOk && resOk && colorOk;
  })();

  const computedPhase = 
    (isFailed || (!isComplete && !isProcessing && !isPending)) ? 1 :
    (isComplete && hasFailedChecks && !autoFixApplied) ? 1 :
    (isComplete && isFastTrack && phase3Confirmed) ? 3 :
    (isComplete && hasProof && !proofChecked) ? 2 :
    (isComplete && (!hasUserSelectedBleed || !preCompileReady || !phase3Confirmed)) ? 2 :
    isComplete ? 3 :
    0;

  const currentPhase = (phaseOverride !== null && phaseOverride < computedPhase) ? phaseOverride : computedPhase;

  const handleStepBack = (steps: number) => {
    const targetPhase = Math.max(1, currentPhase - steps);
    if (targetPhase <= 2) {
      setProofChecked(false);
      setHasDownloaded(false);
      setShareComplete(false);
      setShareEmail('');
      setPhase3Confirmed(false);
      setIsFastTrack(false);
      setFastTrackTriggered(false);
    }
    if (targetPhase <= 1) {
      setBleedChecked(false);
      setComparisonChecked(false);
    }
    setPhaseOverride(targetPhase);
  };

  return (
    <Layout currentPhase={currentPhase} onStepBack={handleStepBack}>
      <div className="max-w-4xl mx-auto">
        <Link href="/" className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors mb-6" data-testid="link-back-dashboard">
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back to Dashboard
        </Link>

        <Card className="p-4 sm:p-5 mb-6 glass-card relative overflow-hidden">
          <div className={`absolute top-0 left-0 w-1 h-full ${
            isComplete && overallPassed ? 'bg-green-500' :
            isComplete ? 'bg-amber-500' :
            job.status === 'failed' ? 'bg-destructive' :
            'bg-primary'
          }`} />

          <div className="flex items-center gap-4">
            <div className="bg-primary/10 p-3 rounded-xl text-primary shrink-0">
              <FileText className="w-6 h-6" />
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="text-lg sm:text-xl font-bold font-display text-foreground truncate" data-testid="text-job-filename">
                {job.filename}
              </h1>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground font-medium mt-1">
                <span className="uppercase tracking-wider bg-muted px-1.5 py-0.5 rounded text-foreground">
                  {job.fileType}
                </span>
                <span>{(job.fileSize / 1024 / 1024).toFixed(2)} MB</span>
                <span>•</span>
                <span>{format(new Date(job.uploadedAt), "MMM d, h:mm a")}</span>
              </div>
            </div>
            <StatusBadge status={job.status as any} overallPassed={job.auditResults?.overallPassed ?? null} />
          </div>
        </Card>

        {(isComplete || isProcessing || isPending || isFailed) && (
          <div className="mb-8" data-testid="phase-stepper">
            <div className="flex items-center justify-between px-2">
              {PHASE_LABELS.map((phase, idx) => {
                const PhaseIcon = phase.icon;
                const isActive = currentPhase === phase.num;
                const isDone = currentPhase > phase.num;
                return (
                  <div key={phase.num} className="flex items-center flex-1">
                    <div className="flex flex-col items-center flex-1">
                      <div className={`w-11 h-11 rounded-full flex items-center justify-center transition-all duration-500 ${
                        isDone ? 'bg-green-500 text-white shadow-lg shadow-green-500/30 scale-100' :
                        isActive ? 'bg-primary text-white shadow-lg shadow-primary/30 scale-110 ring-4 ring-primary/20' :
                        'bg-muted text-muted-foreground'
                      }`}>
                        {isDone ? <CheckCircle2 className="w-5 h-5" /> : <PhaseIcon className="w-5 h-5" />}
                      </div>
                      <span className={`text-xs font-semibold mt-2 text-center transition-colors ${
                        isActive ? 'text-primary' : isDone ? 'text-green-600' : 'text-muted-foreground'
                      }`}>
                        {phase.label}
                      </span>
                    </div>
                    {idx < PHASE_LABELS.length - 1 && (
                      <div className={`h-0.5 flex-1 mx-1 mt-[-18px] rounded-full transition-colors duration-500 ${
                        isDone ? 'bg-green-500' : 'bg-border'
                      }`} />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <AnimatePresence>
          {(isProcessing || isPending || isQueued) && (
            <motion.div 
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
            >
              <Card className={`p-8 mb-8 border-2 text-center ${isQueued ? "border-amber-400/30 bg-amber-50/10" : "border-primary/20 bg-primary/5"}`}>
                <div className={`w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4 ${isQueued ? "bg-amber-400/15" : "bg-primary/15"}`}>
                  <Loader2 className={`w-8 h-8 animate-spin ${isQueued ? "text-amber-500" : "text-primary"}`} />
                </div>
                <h2 className="text-xl font-bold font-display mb-2" data-testid="text-processing-title">
                  {isQueued ? "Queued for processing..." : isProcessing ? "Fixing your artwork..." : "Waiting in queue..."}
                </h2>
                <p className="text-sm text-muted-foreground mb-6 max-w-md mx-auto" data-testid="text-processing-description">
                  {isQueued
                    ? "The press room is busy. Your file is in line and will be processed as soon as a slot opens."
                    : "We're checking fonts, colors, bleeds, and more. This usually takes a few seconds."}
                </p>
                {!isQueued && (
                  <div className="max-w-xs mx-auto">
                    <div className="flex justify-between text-xs font-medium mb-1 text-primary">
                      <span>Processing</span>
                      <span>{Math.round(progress)}%</span>
                    </div>
                    <Progress value={progress} className="h-2.5 bg-primary/10" />
                  </div>
                )}
              </Card>
            </motion.div>
          )}
        </AnimatePresence>

        {currentPhase === 1 && !isProcessing && !isPending && !autoFixApplied && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <Card className="p-6 sm:p-8 mb-6 border-2 border-amber-500/30 bg-gradient-to-br from-amber-50/50 to-orange-50/30 dark:from-amber-500/5 dark:to-orange-500/5">
              <div className="text-center mb-6">
                <div className="w-14 h-14 rounded-full bg-amber-500/15 flex items-center justify-center mx-auto mb-4">
                  <AlertTriangle className="w-7 h-7 text-amber-600" />
                </div>
                <h2 className="text-2xl font-bold font-display mb-2" data-testid="text-phase1-title">
                  {isFailed ? "Processing failed — let's try again" : "Some issues need attention"}
                </h2>
                <p className="text-sm text-muted-foreground max-w-lg mx-auto">
                  {isFailed
                    ? job.errorMessage && !isSafeZoneLayoutError(job.errorMessage)
                      ? job.errorMessage
                      : "Something went wrong during processing. Hit the button below to retry and we'll fix everything automatically."
                    : "We found a few things that could cause problems during printing. Hit the button below and we'll fix everything automatically."}
                </p>
              </div>

              {job.auditResults && job.auditResults.checks.some(c => !c.passed) && (
                <div className="grid gap-3 sm:grid-cols-2 mb-6">
                  {job.auditResults.checks.filter(c => !c.passed).map((check, idx) => (
                    <div key={idx} className="flex items-start gap-3 bg-white/60 dark:bg-background/40 rounded-xl p-4 border border-amber-500/20">
                      <XCircle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
                      <div>
                        <p className="text-sm font-semibold text-foreground">{check.name}</p>
                        <p className="text-xs text-muted-foreground mt-0.5">{check.message}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <div className="text-center">
                <Button 
                  onClick={() => {
                    const audit = job.auditResults as any;
                    const { trimW, trimH } = trimMmFromAudit(audit);
                    const opts: Record<string, any> = {
                      ...bleedOptsObjectFromAudit(audit),
                      targetWidth: trimW,
                      targetHeight: trimH,
                    };
                    const hasCropData = opts.cropX != null && opts.cropWidth > 0;
                    console.log(`[PHASE2] Fix Everything handoff: hasCrop=${hasCropData}, targetSize=${opts.targetWidth}×${opts.targetHeight}mm${hasCropData ? `, crop=(${opts.cropX?.toFixed?.(4)},${opts.cropY?.toFixed?.(4)}) ${opts.cropWidth?.toFixed?.(4)}×${opts.cropHeight?.toFixed?.(4)}` : ''}`);
                    setAutoFixApplied(true);
                    processJob.mutate({ id: job.id, bleedOptions: opts }, {
                      onSuccess: () => {
                        setProofRefreshKey(Date.now());
                        setComparisonRefreshKey(Date.now());
                        setProofLoadError(false);
                        setComparisonLoadError(false);
                        setBleedPreview(null);
                        setBleedPreviewError(null);
                      },
                      onError: (err: unknown) => {
                        if (handleSafeZoneLayoutProcessingError(err)) {
                          return;
                        }
                        const description =
                          err instanceof Error
                            ? err.message
                            : typeof err === "string"
                              ? err
                              : "Processing failed";
                        toast({
                          title: "Processing failed",
                          description,
                          variant: "destructive",
                        });
                      },
                    });
                  }}
                  disabled={processJob.isPending || autoFixApplied}
                  size="lg"
                  className="hover-elevate bg-gradient-to-r from-primary to-primary/90 text-white shadow-xl shadow-primary/25 px-8 h-12 text-base font-bold"
                  data-testid="button-auto-fix"
                >
                  {processJob.isPending ? (
                    <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                  ) : (
                    <Sparkles className="w-5 h-5 mr-2" />
                  )}
                  {isFailed ? "Retry & Fix Automatically" : "Fix Everything Automatically"}
                </Button>
              </div>
            </Card>

            {job.auditResults && (
              <PhaseChecklist checks={job.auditResults.checks} jobId={job.id} filename={job.filename} />
            )}
          </motion.div>
        )}

        {isComplete && currentPhase === 2 && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <Card className="p-6 sm:p-8 mb-6 border-2 border-primary/20 bg-gradient-to-br from-primary/5 to-blue-50/30 dark:from-primary/5 dark:to-blue-500/5">
              <div className="text-center mb-8">
                <div className="w-14 h-14 rounded-full bg-primary/15 flex items-center justify-center mx-auto mb-4">
                  <Eye className="w-7 h-7 text-primary" />
                </div>
                <h2 className="text-2xl font-bold font-display mb-2" data-testid="text-phase2-title">Review your artwork</h2>
                <p className="text-sm text-muted-foreground max-w-lg mx-auto">
                  Please review each section below carefully. You must confirm each one before proceeding to download.
                </p>
              </div>

              {fastTrackEligible && !isFastTrack && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="mb-8 p-5 rounded-2xl border-2 border-green-500/40 bg-gradient-to-r from-green-50/80 to-emerald-50/60 dark:from-green-500/10 dark:to-emerald-500/5 relative overflow-hidden"
                  data-testid="fast-track-banner"
                >
                  <div className="absolute top-0 right-0 w-32 h-32 bg-green-400/10 rounded-full -translate-y-1/2 translate-x-1/2" />
                  <div className="relative flex flex-col sm:flex-row items-center gap-4">
                    <div className="flex items-center gap-3 flex-1 text-center sm:text-left">
                      <div className="w-11 h-11 rounded-full bg-green-500/15 flex items-center justify-center shrink-0">
                        <Zap className="w-6 h-6 text-green-600 dark:text-green-400" />
                      </div>
                      <div>
                        <p className="text-sm font-bold text-green-800 dark:text-green-300" data-testid="text-fast-track-title">
                          Artwork passed all critical checks
                        </p>
                        <p className="text-xs text-green-700/70 dark:text-green-400/60 mt-0.5">
                          Bleed, Resolution, and Color Space are all within spec. You can skip the manual review and go straight to download.
                        </p>
                      </div>
                    </div>
                    <Button
                      size="lg"
                      className="bg-green-600 hover:bg-green-700 text-white gap-2 shadow-lg shadow-green-500/25 hover-elevate whitespace-nowrap"
                      onClick={handleFastTrack}
                      disabled={fastTrackTriggered}
                      data-testid="button-fast-track"
                    >
                      {fastTrackTriggered ? (
                        <><Loader2 className="w-5 h-5 animate-spin" /> Preparing...</>
                      ) : (
                        <><Zap className="w-5 h-5" /> One-Click Approve</>
                      )}
                    </Button>
                  </div>
                  <p className="text-[10px] text-green-600/50 dark:text-green-500/40 mt-3 text-center sm:text-left italic">
                    Full 25-point prepress pipeline still runs — ink color mixing, black sharpening, design locking & font protection are all applied.
                  </p>
                </motion.div>
              )}

              <div className="flex items-center gap-1 p-1 bg-muted/50 rounded-lg mb-6 border border-border/40" data-testid="review-tab-bar">
                <button
                  onClick={() => setReviewTab("review")}
                  className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-md text-sm font-semibold transition-all ${
                    reviewTab === "review"
                      ? "bg-white dark:bg-background shadow-sm text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                  data-testid="tab-review"
                >
                  <Eye className="w-4 h-4" />
                  Review
                </button>
                <button
                  onClick={() => setReviewTab("tools")}
                  className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-md text-sm font-semibold transition-all ${
                    reviewTab === "tools"
                      ? "bg-white dark:bg-background shadow-sm text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                  data-testid="tab-tools"
                >
                  <Wrench className="w-4 h-4" />
                  Tools
                  {(aiEnhanceLoading) && (
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-500" />
                  )}
                </button>
              </div>

              {reviewTab === "review" && (<>

              {jobAudit && (jobAudit.aiEnhanced || jobAudit.lensesDetected || (jobAudit.originalTic > jobAudit.finalTic)) && (
                <div className="mb-6 rounded-xl border-2 border-border/40 bg-white/60 dark:bg-background/40 overflow-hidden" data-testid="resolution-audit-card">
                  <div className="px-4 py-3 border-b border-border/30 flex items-center gap-2">
                    <Sparkles className="w-4 h-4 text-primary" />
                    <span className="text-sm font-bold text-foreground">Pre-flight Summary</span>
                    {(jobAudit.supersampled || jobAudit.aiEnhanced) && (
                      <span className="ml-auto px-2 py-0.5 text-[10px] font-black uppercase tracking-wider rounded-full bg-primary/15 text-primary ai-reconstructed-badge" data-testid="badge-ai-reconstructed">
                        AI Reconstructed
                      </span>
                    )}
                  </div>
                  <div className="divide-y divide-border/20">
                    {jobAudit.aiEnhanced && typeof jobAudit.originalDpi === 'number' && typeof jobAudit.finalDpi === 'number' && (
                      <div className="flex flex-wrap items-center justify-center gap-4 sm:gap-8 px-5 py-4" data-testid="preflight-row-dpi">
                        <span className="sr-only">Resolution increased from {jobAudit.originalDpi} DPI to {jobAudit.finalDpi} DPI via AI enhancement</span>
                        <div className="flex flex-col items-center gap-1 min-w-[80px]" data-testid="audit-col-original">
                          <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">Original DPI</span>
                          <span className="text-xl font-black text-red-500 dark:text-red-400">{Math.round(jobAudit.originalDpi)} <span className="text-xs font-bold">DPI</span></span>
                        </div>
                        <div className="text-xl text-muted-foreground/60 font-bold select-none" aria-hidden="true">&#10132;</div>
                        <div className="flex flex-col items-center gap-1 min-w-[80px]" data-testid="audit-col-enhanced">
                          <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">AI-Enhanced</span>
                          <span className="text-xl font-black text-green-500 dark:text-green-400">{Math.round(jobAudit.finalDpi)} <span className="text-xs font-bold">DPI</span></span>
                        </div>
                      </div>
                    )}
                    {jobAudit.lensesDetected && (
                      <div className="flex flex-wrap items-center justify-center gap-4 sm:gap-8 px-5 py-4" data-testid="preflight-row-lenses">
                        <span className="sr-only">Live transparency effects were detected and flattened for press safety</span>
                        <div className="flex flex-col items-center gap-1 min-w-[80px]">
                          <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">Lenses</span>
                          <span className="text-sm font-black text-red-500 dark:text-red-400">Live Lenses Detected</span>
                        </div>
                        <div className="text-xl text-muted-foreground/60 font-bold select-none" aria-hidden="true">&#10132;</div>
                        <div className="flex flex-col items-center gap-1 min-w-[80px]">
                          <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">Status</span>
                          <span className="text-sm font-black text-green-500 dark:text-green-400">{jobAudit.lensesFlattened ? "Effects Flattened" : "No Fix Needed"}</span>
                        </div>
                      </div>
                    )}
                    {jobAudit.originalTic > jobAudit.finalTic && (
                      <div className="flex flex-wrap items-center justify-center gap-4 sm:gap-8 px-5 py-4" data-testid="preflight-row-ink">
                        <span className="sr-only">Total ink coverage reduced from {jobAudit.originalTic}% to {jobAudit.finalTic}% for press safety</span>
                        <div className="flex flex-col items-center gap-1 min-w-[80px]">
                          <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">Ink Coverage</span>
                          <span className="text-xl font-black text-red-500 dark:text-red-400">{Math.round(jobAudit.originalTic)}% <span className="text-xs font-bold">TIC</span></span>
                        </div>
                        <div className="text-xl text-muted-foreground/60 font-bold select-none" aria-hidden="true">&#10132;</div>
                        <div className="flex flex-col items-center gap-1 min-w-[80px]">
                          <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">Press-Safe</span>
                          <span className="text-xl font-black text-green-500 dark:text-green-400">{Math.round(jobAudit.finalTic)}% <span className="text-xs font-bold">TIC</span></span>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {showLowDpiWarning && !jobAudit?.aiEnhanced && (
                <div className="mb-6 p-3 bg-amber-50 dark:bg-amber-500/10 border border-amber-500/40 rounded-lg flex items-start gap-2" data-testid="alert-low-dpi">
                  <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">Low Resolution Uploaded File ({originalDpi} DPI)</p>
                    <p className="text-xs text-amber-700 dark:text-amber-400 mt-0.5">The uploaded artwork contains images below 150 DPI. Print quality may be affected — we recommend supplying artwork at 300 DPI or higher for best results.</p>
                  </div>
                </div>
              )}

              <div className="space-y-6">
                <div className={`rounded-xl border-2 transition-all ${bleedChecked ? 'border-green-500/40 bg-green-50/20 dark:bg-green-500/5' : 'border-border/60 bg-white/40 dark:bg-background/30'}`} data-testid="section-bleed-preview">
                  <div className="p-4 sm:p-5 border-b border-border/30">
                    <div className="flex items-center gap-3">
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${bleedChecked ? 'bg-green-500 text-white' : 'bg-primary/15 text-primary'}`}>
                        <Scissors className="w-4 h-4" />
                      </div>
                      <div className="flex-1">
                        <h3 className="text-base font-bold text-foreground">Bleed & Cut Line Preview</h3>
                        <p className="text-xs text-muted-foreground">Review the trim lines, safe zones, and bleed boundaries on your artwork.</p>
                        {job.auditResults?.criticalSafeZone && (() => {
                          const checks = job.auditResults?.checks || [];
                          const szCheck = checks.find((c: any) => c.name === "Safe Zone Validation");
                          const details = szCheck?.details || "";
                          const distMatch = details.match(/(\d+\.?\d*)mm/);
                          const closestDist = distMatch ? parseFloat(distMatch[1]) : 999;
                          return closestDist <= 1.0 ? (
                            <span className="inline-flex items-center gap-1 mt-1 px-2 py-0.5 rounded-full bg-red-600 text-white text-xs font-bold" data-testid="badge-critical-safe-zone">
                              <AlertTriangle className="w-3 h-3" />
                              CRITICAL - Text within 1mm of trim line
                            </span>
                          ) : null;
                        })()}
                      </div>
                      {bleedChecked && <CheckCircle2 className="w-6 h-6 text-green-500 shrink-0" />}
                    </div>
                  </div>
                  {(job.auditResults?.rightSafety || job.auditResults?.criticalSafeZone) && (
                    <Collapsible open={openSections.preflight} onOpenChange={(open) => toggleSection("preflight", open)}>
                      <CollapsibleTrigger className="w-full p-4 sm:p-5 border-b border-border/30 cursor-pointer hover:bg-muted/30 transition-colors" data-testid="section-preflight-clearance">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <ShieldCheck className="w-4 h-4 text-green-500" />
                            <h4 className="text-sm font-bold text-foreground">Pre-Flight Clearance</h4>
                            {job.auditResults.rightSafety === "SAFE" ? (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 text-[10px] font-semibold" data-testid="badge-safety-safe">Safe</span>
                            ) : (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 text-[10px] font-semibold" data-testid="badge-safety-critical">Review</span>
                            )}
                          </div>
                          <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform duration-200 ${openSections.preflight ? 'rotate-180' : ''}`} />
                        </div>
                      </CollapsibleTrigger>
                      <LazyCollapsibleContent isOpen={!!openSections.preflight} betaMode={betaMode} resetKey={id}>
                        {job.auditResults?.rightSafety && (
                          <div className="px-4 sm:px-5 py-3 border-b border-border/20">
                            <div className="flex items-center gap-3">
                              <span className="text-xs text-muted-foreground">Right Side:</span>
                              {job.auditResults.rightSafety === "SAFE" ? (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 text-xs font-semibold">
                                  Safe (&gt;1mm from trim)
                                </span>
                              ) : (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 text-xs font-semibold">
                                  Review recommended — content near trim edge
                                </span>
                              )}
                            </div>
                          </div>
                        )}
                        {job.auditResults?.criticalSafeZone && (
                          <div className="px-4 sm:px-5 py-3 border-b border-border/20 bg-amber-50/30 dark:bg-amber-500/5" data-testid="section-auto-shifter">
                            <div className="flex items-center justify-between">
                              <div className="flex-1 mr-4">
                                <div className="flex items-center gap-2">
                                  <Shield className="w-4 h-4 text-amber-500" />
                                  <span className="text-sm font-semibold text-foreground">Auto-Shifter</span>
                                </div>
                                <p className="text-xs text-muted-foreground mt-0.5">Apply a 2% scale-down to pull content away from the trim edge into the safe zone.</p>
                              </div>
                              <Switch
                                checked={autoShifterEnabled}
                                onCheckedChange={setAutoShifterEnabled}
                                data-testid="switch-auto-shifter"
                              />
                            </div>
                          </div>
                        )}
                      </LazyCollapsibleContent>
                    </Collapsible>
                  )}
                  {job.auditResults && job.correctedPath && (
                    <div className="p-4 sm:p-5 border-b border-border/30">
                      <BleedMethodSelector
                        jobId={job.id}
                        variants={job.auditResults.bleedVariants ?? {}}
                        recommended={job.auditResults.recommendedBleedMethod || null}
                        selected={selectedBleedMethod}
                        onSelect={handleBleedMethodSelect}
                        loading={bleedMethodLoading}
                      />
                    </div>
                  )}
                  <div className="p-4 sm:p-5 relative">
                    {prepressOverlay && (
                      <div className="absolute inset-0 z-20 bg-black/40 backdrop-blur-[3px] flex flex-col items-center justify-center gap-3 rounded-lg transition-opacity duration-300" data-testid="prepress-overlay">
                        {prepressOverlay.type === "loading" ? (
                          <>
                            <Loader2 className="w-8 h-8 animate-spin text-white" />
                            <p className="text-sm font-semibold text-white">Processing...</p>
                          </>
                        ) : (
                          <motion.div
                            initial={{ scale: 0.8, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            className="flex flex-col items-center gap-2"
                          >
                            <CheckCircle2 className="w-10 h-10 text-green-400" />
                            <p className="text-sm font-bold text-white" data-testid="text-prepress-success">{prepressOverlay.label}</p>
                          </motion.div>
                        )}
                      </div>
                    )}
                    <BleedPreviewPanel
                      bleedPreview={bleedPreview}
                      bleedPreviewLoading={bleedPreviewLoading}
                      bleedPreviewError={bleedPreviewError}
                      bleedPreviewPage={bleedPreviewPage}
                      setBleedPreviewPage={setBleedPreviewPage}
                      currentBleedPage={currentBleedPage}
                      loadBleedPreview={loadBleedPreview}
                      enhancementLoading={aiEnhanceLoading}
                    />
                  </div>
                  <div className="px-4 sm:px-5 pb-4 sm:pb-5">
                    <label className="flex items-center gap-3 cursor-pointer select-none p-3 rounded-lg bg-white/60 dark:bg-background/40 border border-border/40 hover:border-primary/30 transition-all" data-testid="label-bleed-confirmation">
                      <input
                        type="checkbox"
                        checked={bleedChecked}
                        onChange={(e) => setBleedChecked(e.target.checked)}
                        className="w-5 h-5 rounded border-2 border-primary/40 text-primary focus:ring-primary/30 cursor-pointer accent-primary"
                        data-testid="checkbox-bleed-confirmation"
                      />
                      <span className={`text-sm font-medium transition-colors ${bleedChecked ? 'text-green-700 dark:text-green-400' : 'text-foreground'}`}>
                        I have reviewed the bleed and cut line preview
                      </span>
                    </label>
                  </div>
                </div>

                {hasComparison && (
                  <div className={`rounded-xl border-2 transition-all ${comparisonChecked ? 'border-green-500/40 bg-green-50/20 dark:bg-green-500/5' : 'border-border/60 bg-white/40 dark:bg-background/30'}`} data-testid="section-comparison">
                    <div className="p-4 sm:p-5 border-b border-border/30">
                      <div className="flex items-center gap-3">
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${comparisonChecked ? 'bg-green-500 text-white' : 'bg-primary/15 text-primary'}`}>
                          <Maximize className="w-4 h-4" />
                        </div>
                        <div className="flex-1">
                          <h3 className="text-base font-bold text-foreground">Before vs After</h3>
                          <p className="text-xs text-muted-foreground">Compare your original artwork against the corrected version with fixed bleeds and flattened lenses.</p>
                        </div>
                        {comparisonChecked && <CheckCircle2 className="w-6 h-6 text-green-500 shrink-0" />}
                      </div>
                    </div>
                    <div className="p-4 sm:p-5">
                      <div className="bg-muted/50 rounded-xl border border-border/60 overflow-hidden relative" data-testid="comparison-preview-container">
                        {aiEnhanceLoading && (
                          <div className="absolute inset-0 z-10 bg-white/60 dark:bg-background/60 backdrop-blur-[2px] rounded-lg flex flex-col items-center justify-center gap-2" data-testid="comparison-loading-overlay">
                            <Loader2 className="w-6 h-6 animate-spin text-violet-500" />
                            <p className="text-xs font-medium text-violet-600 dark:text-violet-400">Applying {aiEnhanceLoading.replace(/_/g, ' ')}...</p>
                          </div>
                        )}
                        {!comparisonLoadError ? (
                          <img
                            key={`comparison-${comparisonRefreshKey}`}
                            src={`/api/jobs/${job.id}/comparison?v=${comparisonRefreshKey}`}
                            alt="Sign-Off Comparison - Before vs After"
                            className="w-full h-auto max-h-[600px] object-contain bg-neutral-900"
                            onError={() => setComparisonLoadError(true)}
                            data-testid="img-comparison"
                          />
                        ) : (
                          <div className="flex items-center justify-center h-48 text-muted-foreground">
                            <div className="text-center">
                              <AlertCircle className="w-10 h-10 mx-auto mb-2 opacity-50" />
                              <p className="text-sm">Could not load comparison preview</p>
                            </div>
                          </div>
                        )}
                      </div>
                      <p className="text-xs text-center text-muted-foreground mt-3">
                        Side-by-side: your original artwork (left) vs the corrected version (right) with fixed bleeds.
                      </p>
                    </div>
                    <div className="px-4 sm:px-5 pb-4 sm:pb-5">
                      <label className="flex items-center gap-3 cursor-pointer select-none p-3 rounded-lg bg-white/60 dark:bg-background/40 border border-border/40 hover:border-primary/30 transition-all" data-testid="label-comparison-confirmation">
                        <input
                          type="checkbox"
                          checked={comparisonChecked}
                          onChange={(e) => setComparisonChecked(e.target.checked)}
                          className="w-5 h-5 rounded border-2 border-primary/40 text-primary focus:ring-primary/30 cursor-pointer accent-primary"
                          data-testid="checkbox-comparison-confirmation"
                        />
                        <span className={`text-sm font-medium transition-colors ${comparisonChecked ? 'text-green-700 dark:text-green-400' : 'text-foreground'}`}>
                          I have reviewed the before and after comparison
                        </span>
                      </label>
                    </div>
                  </div>
                )}

                {hasUserSelectedBleed && currentBleedPage && (
                  <div className="mt-4 flex justify-center" data-testid="section-guillotine-trigger">
                    <Button
                      variant="outline"
                      className="gap-2 border-2 border-rose-500/30 bg-rose-50/30 dark:bg-rose-500/5 text-rose-700 dark:text-rose-400 hover:bg-rose-100/50 dark:hover:bg-rose-500/10 hover:border-rose-500/50 font-semibold transition-all hover-elevate"
                      onClick={() => setGuillotineOpen(true)}
                      data-testid="button-guillotine-cut"
                    >
                      <Scissors className="w-4 h-4" />
                      Simulate Guillotine Cut
                    </Button>
                  </div>
                )}

              </div>

              <Collapsible open={openSections.outpaint} onOpenChange={(open) => toggleSection("outpaint", open)}>
                <div className="mt-6 rounded-xl border-2 border-sky-500/20 bg-gradient-to-br from-sky-50/30 to-blue-50/20 dark:from-sky-500/5 dark:to-blue-500/5 overflow-hidden" data-testid="section-expand-background-bleed">
                  <CollapsibleTrigger className="w-full p-4 sm:p-5 cursor-pointer hover:bg-muted/20 transition-colors" data-testid="trigger-outpaint">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-sky-500/15 flex items-center justify-center shrink-0">
                        <Expand className="w-4 h-4 text-sky-600 dark:text-sky-400" />
                      </div>
                      <div className="flex-1 text-left">
                        <h4 className="text-sm font-bold text-foreground">AI Outpaint Bleed</h4>
                        <p className="text-xs text-muted-foreground">Use AI to extend your design edges seamlessly.</p>
                      </div>
                      <PremiumBadge />
                      <span className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-full bg-sky-100 dark:bg-sky-500/15 text-sky-700 dark:text-sky-300" data-testid="badge-generative-bleed">
                        Generative
                      </span>
                      <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform duration-200 ${openSections.outpaint ? 'rotate-180' : ''}`} />
                    </div>
                  </CollapsibleTrigger>
                  <LazyCollapsibleContent isOpen={!!openSections.outpaint} betaMode={betaMode} resetKey={id}>
                    <div className="flex items-center justify-between px-4 sm:px-5 py-4 border-t border-border/20">
                      <div className="flex-1 mr-4">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-foreground">Expand Background</span>
                          {aiEnhanceLoading === "expand_background" && <Loader2 className="w-3.5 h-3.5 animate-spin text-sky-500" />}
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">AI outpainting extends your artwork edges to fill the bleed area naturally — no cropping or stretching needed.</p>
                        {aiEnhanceMessages.expand_background && aiExpandBackground && (
                          <p className="text-[11px] text-sky-600 dark:text-sky-400 mt-1 italic" data-testid="text-expand-background-status">{aiEnhanceMessages.expand_background}</p>
                        )}
                      </div>
                      <Switch
                        checked={aiExpandBackground}
                        onCheckedChange={(checked) => handlePremiumToggle("expand_background", checked)}
                        disabled={!!aiEnhanceLoading}
                        data-testid="switch-expand-background"
                      />
                    </div>
                  </LazyCollapsibleContent>
                </div>
              </Collapsible>

              {(() => {
                const isPdfFile = job.filename?.toLowerCase().endsWith('.pdf');
                return (
                  <Collapsible open={openSections.prepress} onOpenChange={(open) => toggleSection("prepress", open)}>
                  <div className="mt-6 rounded-xl border-2 border-blue-500/20 bg-gradient-to-br from-blue-50/30 to-slate-50/20 dark:from-blue-500/5 dark:to-slate-500/5 overflow-hidden" data-testid="section-prepress-refinements">
                    <CollapsibleTrigger className="w-full p-4 sm:p-5 cursor-pointer hover:bg-muted/20 transition-colors" data-testid="trigger-prepress">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-blue-500/15 flex items-center justify-center shrink-0">
                          <Shield className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                        </div>
                        <div className="flex-1 text-left">
                          <h4 className="text-sm font-bold text-foreground" data-testid="text-prepress-title">Prepress Refinements</h4>
                          <p className="text-xs text-muted-foreground">Press-specific fixes for your pixel data.</p>
                        </div>
                        <span className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-full bg-blue-100 dark:bg-blue-500/15 text-blue-700 dark:text-blue-300" data-testid="badge-prepress">
                          Prepress
                        </span>
                        <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform duration-200 ${openSections.prepress ? 'rotate-180' : ''}`} />
                      </div>
                    </CollapsibleTrigger>
                    <LazyCollapsibleContent isOpen={!!openSections.prepress} betaMode={betaMode} resetKey={id}>
                    <div className="divide-y divide-border/20">
                      <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-tac-limit">
                        <div className="flex-1 mr-4">
                          <div className="flex items-center gap-2">
                            <Droplets className="w-3.5 h-3.5 text-amber-500" />
                            <span className="text-sm font-semibold text-foreground">Prevent soggy paper (Safe Ink limit)</span>
                            {aiEnhanceLoading === "tac_limit" && <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />}
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">Caps total ink coverage at 280% to prevent paper from getting too wet during litho printing.</p>
                          {isPdfFile && (
                            <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-1 italic">PDF detected — this tool works best on raster images (PNG/JPG). Results may be limited.</p>
                          )}
                          {aiEnhanceMessages.tac_limit && aiTacLimit && (
                            <p className="text-[11px] text-blue-600 dark:text-blue-400 mt-1 italic" data-testid="text-tac-limit-status">{aiEnhanceMessages.tac_limit}</p>
                          )}
                        </div>
                        <Switch
                          checked={aiTacLimit}
                          onCheckedChange={(checked) => handleAiEnhancementToggle("tac_limit", checked)}
                          disabled={!!aiEnhanceLoading}
                          data-testid="switch-tac-limit"
                        />
                      </div>
                      <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-trapping">
                        <div className="flex-1 mr-4">
                          <div className="flex items-center gap-2">
                            <Shield className="w-3.5 h-3.5 text-blue-500" />
                            <span className="text-sm font-semibold text-foreground">Close white print gaps</span>
                            {aiEnhanceLoading === "trapping" && <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />}
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">Adds tiny overlap between colours so white gaps don't appear if the press is slightly off-register.</p>
                          {isPdfFile && (
                            <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-1 italic">PDF detected — this tool works best on raster images (PNG/JPG). Results may be limited.</p>
                          )}
                          {aiEnhanceMessages.trapping && aiTrapping && (
                            <p className="text-[11px] text-blue-600 dark:text-blue-400 mt-1 italic" data-testid="text-trapping-status">{aiEnhanceMessages.trapping}</p>
                          )}
                        </div>
                        <Switch
                          checked={aiTrapping}
                          onCheckedChange={(checked) => handleAiEnhancementToggle("trapping", checked)}
                          disabled={!!aiEnhanceLoading}
                          data-testid="switch-trapping"
                        />
                      </div>
                    </div>
                    <div className="px-4 sm:px-5 py-3 bg-blue-50/50 dark:bg-blue-500/5 border-t border-border/20">
                      <p className="text-[10px] text-muted-foreground text-center italic">
                        Prepress operations modify pixel data directly using local processing. Your original artwork is preserved.
                      </p>
                    </div>
                    </LazyCollapsibleContent>
                  </div>
                  </Collapsible>
                );
              })()}

              </>)}

              {reviewTab === "tools" && (<>

              <Collapsible open={openSections.aiEnhancements} onOpenChange={(open) => toggleSection("aiEnhancements", open)}>
              <div className="rounded-xl border-2 border-violet-500/20 bg-gradient-to-br from-violet-50/30 to-purple-50/20 dark:from-violet-500/5 dark:to-purple-500/5 overflow-hidden" data-testid="section-ai-enhancements">
                <CollapsibleTrigger className="w-full p-4 sm:p-5 cursor-pointer hover:bg-muted/20 transition-colors" data-testid="trigger-ai-enhancements">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-violet-500/15 flex items-center justify-center shrink-0">
                      <Sparkles className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                    </div>
                    <div className="flex-1 text-left">
                      <h3 className="text-base font-bold text-foreground" data-testid="text-ai-enhancements-title">AI Enhancements</h3>
                      <p className="text-xs text-muted-foreground">Optional AI upgrades — your original is always preserved.</p>
                    </div>
                    <span className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-full bg-violet-100 dark:bg-violet-500/15 text-violet-700 dark:text-violet-300" data-testid="badge-opt-in">
                      Opt-In
                    </span>
                    <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform duration-200 ${openSections.aiEnhancements ? 'rotate-180' : ''}`} />
                  </div>
                </CollapsibleTrigger>
                <LazyCollapsibleContent isOpen={!!openSections.aiEnhancements} betaMode={betaMode} resetKey={id}>
                <div className="divide-y divide-border/20">
                  <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-denoise">
                    <div className="flex-1 mr-4">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-foreground">Clean up photo grain</span>
                        <PremiumBadge />
                        {aiEnhanceLoading === "denoise" && <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-500" />}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">Reduce noise and grain from photos while preserving sharpness.</p>
                      {aiEnhanceMessages.denoise && aiDenoise && (
                        <p className="text-[11px] text-violet-600 dark:text-violet-400 mt-1 italic" data-testid="text-denoise-status">{aiEnhanceMessages.denoise}</p>
                      )}
                    </div>
                    <Switch
                      checked={aiDenoise}
                      onCheckedChange={(checked) => handlePremiumToggle("denoise", checked)}
                      disabled={!!aiEnhanceLoading}
                      data-testid="switch-denoise"
                    />
                  </div>
                  <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-sharpen-logos">
                    <div className="flex-1 mr-4">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-foreground">Sharpen blurry logos</span>
                        <PremiumBadge />
                        {aiEnhanceLoading === "sharpen_logos" && <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-500" />}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">Detect and sharpen blurry logos using AI edge enhancement.</p>
                      {aiEnhanceMessages.sharpen_logos && aiSharpenLogos && (
                        <p className="text-[11px] text-violet-600 dark:text-violet-400 mt-1 italic" data-testid="text-sharpen-status">{aiEnhanceMessages.sharpen_logos}</p>
                      )}
                    </div>
                    <Switch
                      checked={aiSharpenLogos}
                      onCheckedChange={(checked) => handlePremiumToggle("sharpen_logos", checked)}
                      disabled={!!aiEnhanceLoading}
                      data-testid="switch-sharpen-logos"
                    />
                  </div>
                  <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-spell-check">
                    <div className="flex-1 mr-4">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-foreground">Check spelling (SA Languages)</span>
                        <PremiumBadge />
                        {aiEnhanceLoading === "spell_check" && <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-500" />}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">OCR-based spell check for English, Afrikaans, Zulu, Xhosa, and Sotho.</p>
                      {aiEnhanceMessages.spell_check && aiSpellCheck && (
                        <p className="text-[11px] text-violet-600 dark:text-violet-400 mt-1 italic" data-testid="text-spell-check-status">{aiEnhanceMessages.spell_check}</p>
                      )}
                    </div>
                    <Switch
                      checked={aiSpellCheck}
                      onCheckedChange={(checked) => handlePremiumToggle("spell_check", checked)}
                      disabled={!!aiEnhanceLoading}
                      data-testid="switch-spell-check"
                    />
                  </div>
                </div>
                <div className="px-4 sm:px-5 py-3 bg-violet-50/50 dark:bg-violet-500/5 border-t border-border/20">
                  <p className="text-[10px] text-muted-foreground text-center italic">
                    All enhancements are non-destructive. Your original artwork is preserved and you can revert at any time by toggling off.
                  </p>
                </div>
                </LazyCollapsibleContent>
              </div>
              </Collapsible>

              <Collapsible open={openSections.marketing} onOpenChange={(open) => toggleSection("marketing", open)}>
              <div className="mt-6 rounded-xl border-2 border-amber-500/20 bg-gradient-to-br from-amber-50/30 to-orange-50/20 dark:from-amber-500/5 dark:to-orange-500/5 overflow-hidden" data-testid="section-power-ups">
                <CollapsibleTrigger className="w-full p-4 sm:p-5 cursor-pointer hover:bg-muted/20 transition-colors" data-testid="trigger-marketing">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-amber-500/15 flex items-center justify-center shrink-0">
                      <Zap className="w-4 h-4 text-amber-600 dark:text-amber-400" />
                    </div>
                    <div className="flex-1 text-left">
                      <h3 className="text-base font-bold text-foreground" data-testid="text-power-ups-title">Marketing & Design</h3>
                      <p className="text-xs text-muted-foreground">Advanced tools to level up your design.</p>
                    </div>
                    <span className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-full bg-amber-100 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300" data-testid="badge-power-ups">
                      Power-Ups
                    </span>
                    <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform duration-200 ${openSections.marketing ? 'rotate-180' : ''}`} />
                  </div>
                </CollapsibleTrigger>
                <LazyCollapsibleContent isOpen={!!openSections.marketing} betaMode={betaMode} resetKey={id}>
                <div className="divide-y divide-border/20">
                  <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-engagement-score">
                    <div className="flex-1 mr-4">
                      <div className="flex items-center gap-2">
                        <Target className="w-3.5 h-3.5 text-rose-500" />
                        <span className="text-sm font-semibold text-foreground">Check Eye-Catching Score</span>
                        {aiEnhanceLoading === "engagement_score" && <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-500" />}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">Get a score out of 100 showing how eye-catching your design is, with tips to improve it.</p>
                      {aiEnhanceMessages.engagement_score && aiEngagementScore && (
                        <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-1 italic" data-testid="text-engagement-score-status">{aiEnhanceMessages.engagement_score}</p>
                      )}
                    </div>
                    <Switch
                      checked={aiEngagementScore}
                      onCheckedChange={(checked) => handleAiEnhancementToggle("engagement_score", checked)}
                      disabled={!!aiEnhanceLoading}
                      data-testid="switch-engagement-score"
                    />
                  </div>
                  <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-background-remove">
                    <div className="flex-1 mr-4">
                      <div className="flex items-center gap-2">
                        <Eraser className="w-3.5 h-3.5 text-teal-500" />
                        <span className="text-sm font-semibold text-foreground">Clean Background</span>
                        <PremiumBadge />
                        {aiEnhanceLoading === "background_remove" && <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-500" />}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">Remove the background from your artwork, leaving just the main subject on a clean transparent layer.</p>
                      {aiEnhanceMessages.background_remove && aiBackgroundRemove && (
                        <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-1 italic" data-testid="text-background-remove-status">{aiEnhanceMessages.background_remove}</p>
                      )}
                    </div>
                    <Switch
                      checked={aiBackgroundRemove}
                      onCheckedChange={(checked) => handlePremiumToggle("background_remove", checked)}
                      disabled={!!aiEnhanceLoading}
                      data-testid="switch-background-remove"
                    />
                  </div>
                  <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-text-reconstruct">
                    <div className="flex-1 mr-4">
                      <div className="flex items-center gap-2">
                        <Type className="w-3.5 h-3.5 text-indigo-500" />
                        <span className="text-sm font-semibold text-foreground">Make Text Razor Sharp</span>
                        <PremiumBadge />
                        {aiEnhanceLoading === "text_reconstruct" && <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-500" />}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">Apply an unsharp mask to crisp up text edges and fine details for cleaner print output.</p>
                      {aiEnhanceMessages.text_reconstruct && aiTextReconstruct && (
                        <p className="text-[11px] text-violet-600 dark:text-violet-400 mt-1 italic" data-testid="text-reconstruct-status">{aiEnhanceMessages.text_reconstruct}</p>
                      )}
                    </div>
                    <Switch
                      checked={aiTextReconstruct}
                      onCheckedChange={(checked) => handleAiEnhancementToggle("text_reconstruct", checked)}
                      disabled={!!aiEnhanceLoading}
                      data-testid="switch-text-reconstruct"
                    />
                  </div>
                  <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-identify-fonts">
                    <div className="flex-1 mr-4">
                      <div className="flex items-center gap-2">
                        <ScanSearch className="w-3.5 h-3.5 text-emerald-500" />
                        <span className="text-sm font-semibold text-foreground">Identify Fonts</span>
                        <PremiumBadge />
                        {aiEnhanceLoading === "identify_fonts" && <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-500" />}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">Automatically detect and identify the fonts used in your artwork for accurate reproduction.</p>
                      {aiEnhanceMessages.identify_fonts && aiIdentifyFonts && (
                        <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-1 italic" data-testid="text-identify-fonts-status">{aiEnhanceMessages.identify_fonts}</p>
                      )}
                    </div>
                    <Switch
                      checked={aiIdentifyFonts}
                      onCheckedChange={(checked) => handlePremiumToggle("identify_fonts", checked)}
                      disabled={!!aiEnhanceLoading}
                      data-testid="switch-identify-fonts"
                    />
                  </div>
                  <div className="flex items-center justify-between px-4 sm:px-5 py-4" data-testid="toggle-row-test-design-style">
                    <div className="flex-1 mr-4">
                      <div className="flex items-center gap-2">
                        <Palette className="w-3.5 h-3.5 text-pink-500" />
                        <span className="text-sm font-semibold text-foreground">Test Design Style</span>
                        <PremiumBadge />
                        {aiEnhanceLoading === "test_design_style" && <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-500" />}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">A/B test your design — find out if it reads as Club or Corporate, with tips to shift the vibe.</p>
                      {aiEnhanceMessages.test_design_style && aiTestDesignStyle && (
                        <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-1 italic" data-testid="text-test-design-style-status">{aiEnhanceMessages.test_design_style}</p>
                      )}
                    </div>
                    <Switch
                      checked={aiTestDesignStyle}
                      onCheckedChange={(checked) => handlePremiumToggle("test_design_style", checked)}
                      disabled={!!aiEnhanceLoading}
                      data-testid="switch-test-design-style"
                    />
                  </div>
                </div>
                <div className="px-4 sm:px-5 py-3 bg-amber-50/50 dark:bg-amber-500/5 border-t border-border/20">
                  <p className="text-[10px] text-muted-foreground text-center italic">
                    These features will be powered by external AI services. Your original artwork is always preserved.
                  </p>
                </div>
                </LazyCollapsibleContent>
              </div>
              </Collapsible>

              </>)}

              <div className="mt-8 pt-6 border-t border-border/40">
                <div className="text-center">
                  {allReviewsChecked ? (
                    <motion.div
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                    >
                      <p className="text-sm text-green-700 dark:text-green-400 font-medium flex items-center justify-center gap-2 mb-4">
                        <CheckCircle2 className="w-5 h-5" />
                        All sections reviewed — you're ready to proceed
                      </p>
                    </motion.div>
                  ) : (
                    <>
                      <p className="text-sm text-muted-foreground flex items-center justify-center gap-2 mb-3">
                        <AlertCircle className="w-4 h-4" />
                        {prepressSpinnerActive ? "Processing prepress refinement..." : "Please review and confirm all sections above to proceed"}
                      </p>
                      <div className="flex items-center justify-center gap-3 mb-4">
                        <span className={`text-xs px-2 py-1 rounded-full ${bleedChecked ? 'bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400' : 'bg-muted text-muted-foreground'}`}>
                          {bleedChecked ? '✓' : '○'} Bleed Preview
                        </span>
                        {hasComparison && (
                          <span className={`text-xs px-2 py-1 rounded-full ${comparisonChecked ? 'bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400' : 'bg-muted text-muted-foreground'}`}>
                            {comparisonChecked ? '✓' : '○'} Before & After
                          </span>
                        )}
                        {prepressSpinnerActive && (
                          <span className="text-xs px-2 py-1 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400 flex items-center gap-1">
                            <Loader2 className="w-3 h-3 animate-spin" /> Prepress
                          </span>
                        )}
                      </div>
                    </>
                  )}

                  {(textClearupOffer || textClearupPhase === "editing" || textClearupPhase === "ocr" || textClearupPhase === "applying" || textClearupPhase === "detecting" || textClearupPhase === "done") && (
                    <Card className="mb-5 p-4 border-2 border-violet-400/40 bg-violet-50/40 dark:bg-violet-500/5" data-testid="card-text-clearup">
                      <div className="flex items-start gap-3 mb-3">
                        <Type className="w-5 h-5 text-violet-600 shrink-0 mt-0.5" />
                        <div className="flex-1 min-w-0">
                          <h3 className="text-sm font-bold text-foreground">Optional: Text Clear-up</h3>
                          <p className="text-xs text-muted-foreground mt-1">
                            Soft or AI-looking text was detected. You can OCR it, edit any mistakes, then overlay sharp type — or skip and continue as usual.
                          </p>
                          {textClearupReason && textClearupPhase === "offered" && (
                            <p className="text-[11px] text-violet-700 dark:text-violet-300 mt-1 italic">{textClearupReason}</p>
                          )}
                          {textClearupError && (
                            <p className="text-[11px] text-destructive mt-1">{textClearupError}</p>
                          )}
                        </div>
                      </div>

                      {textClearupPhase === "detecting" && (
                        <p className="text-xs text-muted-foreground flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Checking text sharpness…</p>
                      )}

                      {textClearupPhase === "offered" && (
                        <div className="flex flex-wrap gap-2">
                          <Button size="sm" className="gap-1.5" onClick={handleTextClearupStartOcr} disabled={textClearupBusy} data-testid="button-text-clearup-start">
                            <Sparkles className="w-3.5 h-3.5" /> Clear up text (optional)
                          </Button>
                          <Button size="sm" variant="outline" onClick={handleTextClearupSkip} disabled={textClearupBusy} data-testid="button-text-clearup-skip">
                            Skip — keep as is
                          </Button>
                        </div>
                      )}

                      {(textClearupPhase === "ocr" || textClearupPhase === "applying") && (
                        <p className="text-xs text-muted-foreground flex items-center gap-2">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          {textClearupPhase === "ocr" ? "Reading text (OCR)…" : "Applying sharp overlay & refreshing print file…"}
                        </p>
                      )}

                      {textClearupPhase === "editing" && (
                        <div className="space-y-3" data-testid="text-clearup-editor">
                          <p className="text-[11px] text-muted-foreground">
                            Edit only if OCR misread something. Spelling is not auto-corrected. Untick a row to leave that soft text unchanged.
                          </p>
                          <div className="max-h-64 overflow-y-auto space-y-2 rounded-md border border-border/40 bg-background/60 p-2">
                            {textClearupBlocks.map((block, idx) => (
                              <div key={block.id || idx} className="flex gap-2 items-start">
                                <input
                                  type="checkbox"
                                  className="mt-2"
                                  checked={block.include !== false}
                                  onChange={(e) => {
                                    const checked = e.target.checked;
                                    setTextClearupBlocks((prev) => prev.map((b, i) => i === idx ? { ...b, include: checked } : b));
                                  }}
                                  data-testid={`text-clearup-include-${idx}`}
                                />
                                <Textarea
                                  value={block.text}
                                  onChange={(e) => {
                                    const val = e.target.value;
                                    setTextClearupBlocks((prev) => prev.map((b, i) => i === idx ? { ...b, text: val } : b));
                                  }}
                                  className="min-h-[52px] text-xs font-mono"
                                  data-testid={`text-clearup-text-${idx}`}
                                />
                              </div>
                            ))}
                            {textClearupBlocks.length === 0 && (
                              <p className="text-xs text-muted-foreground p-2">No text blocks found.</p>
                            )}
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <Button size="sm" onClick={handleTextClearupApply} disabled={textClearupBusy} data-testid="button-text-clearup-apply">
                              Apply sharp text
                            </Button>
                            <Button size="sm" variant="outline" onClick={handleTextClearupSkip} disabled={textClearupBusy} data-testid="button-text-clearup-cancel">
                              Cancel / skip
                            </Button>
                          </div>
                        </div>
                      )}

                      {textClearupPhase === "done" && (
                        <p className="text-xs text-green-700 dark:text-green-400 flex items-center gap-1.5">
                          <CheckCircle2 className="w-3.5 h-3.5" /> Sharp text applied — print file refreshing.
                        </p>
                      )}
                    </Card>
                  )}

                  <Button
                    size="lg"
                    className={`gap-2 transition-all duration-300 ${
                      allReviewsChecked && hasUserSelectedBleed && preCompileReady
                        ? "bg-green-600 hover:bg-green-700 text-white shadow-lg shadow-green-500/20 hover-elevate"
                        : "bg-muted text-muted-foreground cursor-not-allowed"
                    }`}
                    disabled={!allReviewsChecked || !hasUserSelectedBleed || !preCompileReady}
                    onClick={() => {
                      setProofChecked(true);
                      setPhase3Confirmed(true);
                      setPhaseOverride(null);

                      const methodLabel = BLEED_METHOD_LABELS[selectedBleedMethod as keyof typeof BLEED_METHOD_LABELS]?.label || selectedBleedMethod;
                      const audit = job.auditResults?.jobAudit;
                      const auditResults = job.auditResults as any;
                      const dlUrl = compileDownloadUrl || `/api/jobs/${job.id}/download-bundle?strategy=${encodeURIComponent(selectedBleedMethod)}`;

                      window.dispatchEvent(new CustomEvent("glitchy:compile-complete", { detail: {
                        downloadUrl: dlUrl,
                        lensesDetected: audit?.lensesDetected ?? false,
                        lensesFlattened: audit?.lensesFlattened ?? false,
                        supersampled: audit?.supersampled ?? false,
                        aiEnhanced: audit?.aiEnhanced ?? false,
                        originalTic: audit?.originalTic ?? null,
                        finalTic: audit?.finalTic ?? null,
                        auditReport: auditResults?.compileAuditReport || null,
                        glitchyMessage: `Your artwork is compiled with ${methodLabel} bleed and ready for the press!`,
                        glitchyState: "triumphant",
                        selectedBleedMethod: methodLabel,
                      } }));
                    }}
                    data-testid="button-proceed-to-download"
                  >
                    {(prepressSpinnerActive || !hasUserSelectedBleed || (preCompileState === "compiling")) ? (
                      <><Loader2 className="w-5 h-5 animate-spin" /> Preparing artwork...</>
                    ) : !allReviewsChecked ? (
                      <>Review required to proceed</>
                    ) : (
                      <>Proceed to Download <ArrowRight className="w-5 h-5" /></>
                    )}
                  </Button>
                </div>
              </div>
            </Card>
          </motion.div>
        )}

        {isComplete && currentPhase === 3 && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <Card className="p-6 sm:p-8 mb-6 border-2 border-green-500/30 bg-gradient-to-br from-green-50/50 to-emerald-50/30 dark:from-green-500/5 dark:to-emerald-500/5 relative overflow-hidden">
              <div className="absolute inset-0 opacity-5">
                <div className="absolute inset-0" style={{
                  backgroundImage: `radial-gradient(circle at 20% 50%, rgba(34, 197, 94, 0.3), transparent 50%), radial-gradient(circle at 80% 50%, rgba(16, 185, 129, 0.2), transparent 50%)`
                }} />
              </div>

              <div className="relative text-center mb-8">
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: "spring", stiffness: 200, damping: 15, delay: 0.2 }}
                  className="w-20 h-20 rounded-full bg-green-500 text-white flex items-center justify-center mx-auto mb-5 shadow-xl shadow-green-500/30"
                >
                  <PartyPopper className="w-10 h-10" />
                </motion.div>
                <motion.h2
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.3 }}
                  className="text-3xl font-extrabold font-display mb-3" data-testid="text-phase3-title"
                >
                  Your artwork is <span className="text-transparent bg-clip-text bg-gradient-to-r from-green-600 to-emerald-500">print-ready!</span>
                </motion.h2>
              </div>

              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.4 }}
                className="relative space-y-4 max-w-lg mx-auto"
              >
                <Card className="p-5 border-2 border-green-500/30 bg-white dark:bg-background" data-testid="card-download-bundle">
                  <div className="flex items-center gap-4 mb-4">
                    <div className="w-12 h-12 rounded-xl bg-green-500 text-white flex items-center justify-center shrink-0 shadow-lg shadow-green-500/30">
                      <Download className="w-6 h-6" />
                    </div>
                    <div className="text-left flex-1">
                      <h3 className="font-bold text-foreground mb-0.5">Download Print Ready Artwork</h3>
                      <p className="text-xs text-muted-foreground">
                        Press-ready PDF with perfect ink colors, trim marks and visual proof
                      </p>
                    </div>
                  </div>

                  {compileState && compileState !== "COMPLETE" && compileState !== "FAILURE" && (
                    <div className="mb-4 space-y-3" data-testid="compile-progress">
                      <div className="flex items-center gap-3 text-sm">
                        <Loader2 className="w-4 h-4 animate-spin text-green-500" />
                        <span className="text-muted-foreground font-medium">{
                          compileMessage.includes("Retrieving") ? "Making your artwork razor-sharp..." :
                          compileMessage.includes("Generating") || compileMessage.includes("bleed") || compileMessage.includes("Applying bleed") ? "Stretching the edges for a clean cut..." :
                          compileMessage.includes("Packaging image") || compileMessage.includes("Flattening") || compileMessage.includes("flatten") ? "Locking your design in place..." :
                          compileMessage.includes("trim") || compileMessage.includes("Trim") ? "Marking where the cutter must trim..." :
                          compileMessage.includes("Converting") || compileMessage.includes("Neutralizing") || compileMessage.includes("CMYK") ? "Mixing the perfect ink colors..." :
                          compileMessage.includes("Trapping") || compileMessage.includes("trap") ? "Closing up tiny print gaps..." :
                          compileMessage.includes("Packaging") || compileMessage.includes("packaging") ? "Cleaning up and packaging the final file..." :
                          compileMessage.includes("Waiting") ? "Waiting in the queue..." :
                          compileMessage || "Preparing your artwork..."
                        }</span>
                      </div>
                      <div className="w-full h-1.5 bg-primary/10 rounded-full overflow-hidden mb-1">
                        <div className="h-full bg-green-500 rounded-full animate-pulse transition-all duration-700 ease-in-out" style={{
                          width: `${compileMessage.includes("Retrieving") ? 10
                            : compileMessage.includes("Generating") || compileMessage.includes("bleed") || compileMessage.includes("Applying bleed") ? 25
                            : compileMessage.includes("Packaging image") || compileMessage.includes("Flattening") || compileMessage.includes("flatten") ? 40
                            : compileMessage.includes("trim") || compileMessage.includes("Trim") ? 55
                            : compileMessage.includes("Converting") || compileMessage.includes("Neutralizing") || compileMessage.includes("CMYK") ? 70
                            : compileMessage.includes("Trapping") || compileMessage.includes("trap") ? 80
                            : compileMessage.includes("Packaging") || compileMessage.includes("packaging") ? 90 : 5}%`
                        }} />
                      </div>
                      <p className="text-[10px] text-muted-foreground/60">This usually takes 15-30 seconds — hang tight</p>
                      <div className="space-y-1.5">
                        {[
                          "Making your artwork razor-sharp...",
                          "Stretching the edges for a clean cut...",
                          "Locking your design in place...",
                          "Marking where the cutter must trim...",
                          "Mixing the perfect ink colors...",
                          "Closing up tiny print gaps...",
                          "Cleaning up and packaging the final file..."
                        ].map((stage, i) => {
                          const currentIdx = compileMessage.includes("Retrieving") ? 0
                            : compileMessage.includes("Generating") || compileMessage.includes("bleed") || compileMessage.includes("Applying bleed") ? 1
                            : compileMessage.includes("Packaging image") || compileMessage.includes("Flattening") || compileMessage.includes("flatten") ? 2
                            : compileMessage.includes("trim") || compileMessage.includes("Trim") ? 3
                            : compileMessage.includes("Converting") || compileMessage.includes("Neutralizing") || compileMessage.includes("CMYK") ? 4
                            : compileMessage.includes("Trapping") || compileMessage.includes("trap") ? 5
                            : compileMessage.includes("Packaging") || compileMessage.includes("packaging") ? 6 : -1;
                          const isDone = i < currentIdx;
                          const isActive = i === currentIdx;
                          return (
                            <div key={i} className={`flex items-center gap-2 text-xs py-1 px-2 rounded ${isActive ? 'bg-green-50 dark:bg-green-500/10 text-green-700 dark:text-green-300 font-medium' : isDone ? 'text-green-600 dark:text-green-400' : 'text-muted-foreground/50'}`} data-testid={`compile-stage-${i}`}>
                              {isDone ? <CheckCircle2 className="w-3.5 h-3.5" /> : isActive ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <div className="w-3.5 h-3.5 rounded-full border border-muted-foreground/30" />}
                              <span>{stage}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {compileState === "FAILURE" && (
                    <div className="mb-4 p-3 bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 rounded-lg" data-testid="compile-error">
                      <div className="flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
                        <XCircle className="w-4 h-4 shrink-0" />
                        <span>{compileError || "Compilation failed"}</span>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        className="mt-2 text-xs border-red-300 dark:border-red-500/40"
                        data-testid="button-retry-compile"
                        onClick={() => {
                          setCompileState(null);
                          setCompileError(null);
                          setPreCompileState(null);
                          setPreCompileMessage("");
                          noneCountRef.current = 0;
                          handleBleedMethodSelect(selectedBleedMethod, true);
                        }}
                      >
                        <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
                        Retry Compilation
                      </Button>
                    </div>
                  )}

                  {preCompileReady ? (
                    <>
                      <a
                        href={`/api/jobs/${job.id}/download-bundle?strategy=${encodeURIComponent(selectedBleedMethod)}`}
                        download="Print Ready Artwork.zip"
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={() => { setHasDownloaded(true); }}
                        className="w-full bg-green-600 hover:bg-green-700 text-white gap-2 shadow-md shadow-green-500/20 text-base py-5 inline-flex items-center justify-center rounded-md font-medium transition-colors"
                        data-testid="button-download-print-ready"
                      >
                        <Download className="w-5 h-5 mr-2" /> Download Print Ready Artwork
                      </a>
                      <div className="mt-3 rounded-lg border border-[#a3e635]/40 bg-[#a3e635]/10 p-3" data-testid="preflight-summary">
                        <p className="text-xs font-semibold text-foreground mb-2">Pre-flight Summary</p>
                        <ul className="space-y-1 text-xs">
                          <li data-testid="preflight-ink" className="flex items-center gap-1.5">
                            <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" style={{ color: "#a3e635" }} />
                            <span className="text-muted-foreground">Ink Coverage: Clamped to 200% TIC (Press-Safe)</span>
                          </li>
                          <li data-testid="preflight-text" className="flex items-center gap-1.5">
                            <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" style={{ color: "#a3e635" }} />
                            <span className="text-muted-foreground">Text Sharpening: Converted to 100% K Overprint</span>
                          </li>
                          <li data-testid="preflight-bleed" className="flex items-center gap-1.5">
                            <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" style={{ color: "#a3e635" }} />
                            <span className="text-muted-foreground">Bleed Status: 5mm TrimBox & BleedBox Embedded</span>
                          </li>
                        </ul>
                      </div>

                      {(() => {
                        const tacResult = (job.auditResults as any)?.aiEnhancements?.tac_limit?.result;
                        const maxTac = tacResult?.max_tac_found || 0;
                        return maxTac > 240 ? (
                          <div className="mt-3 rounded-lg border border-amber-400/40 bg-amber-50 dark:bg-amber-500/10 p-3" data-testid="dry-time-warning">
                            <div className="flex items-start gap-2">
                              <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                              <div>
                                <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">Note for Printer: High ink density</p>
                                <p className="text-xs text-amber-700 dark:text-amber-400 mt-0.5" data-testid="text-dry-time">
                                  TAC measured at {Math.round(maxTac)}%. Let dry for 4–6 hours before cutting to prevent smudging.
                                </p>
                              </div>
                            </div>
                          </div>
                        ) : null;
                      })()}

                      <div className="mt-4 rounded-lg border border-violet-300/30 bg-violet-50/50 dark:bg-violet-500/10 p-4 text-center" data-testid="ar-proof-qr-section">
                        <div className="flex items-center justify-center gap-2 mb-2">
                          <Eye className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                          <p className="text-xs font-semibold text-foreground">AR Print Preview</p>
                        </div>
                        <p className="text-[11px] text-muted-foreground mb-3">
                          Scan with your phone to see your flyer in augmented reality — choose between glossy and matte finishes.
                        </p>
                        <div className="inline-block p-3 bg-white rounded-xl shadow-sm" data-testid="ar-qr-code">
                          <QRCodeSVG
                            value={`${window.location.origin}/ar-proof/${job.id}`}
                            size={120}
                            level="M"
                            includeMargin={false}
                          />
                        </div>
                        <p className="text-[10px] text-muted-foreground mt-2 italic">
                          All 3D rendering runs on your device — zero server load.
                        </p>
                      </div>
                    </>
                  ) : preCompileState === "compiling" ? (
                    <div className="space-y-2" data-testid="precompile-progress">
                      <Button
                        className="w-full bg-green-600 hover:bg-green-700 text-white gap-2 shadow-md shadow-green-500/20 text-base py-5"
                        disabled
                        data-testid="button-download-print-ready"
                      >
                        <Loader2 className="w-5 h-5 animate-spin" /> Getting your artwork ready... {preCompileMessage}
                      </Button>
                      <div className="w-full h-1 bg-green-100 dark:bg-green-900/30 rounded-full overflow-hidden">
                        <div className="h-full bg-green-500 rounded-full animate-[pulse_1.5s_ease-in-out_infinite]" style={{ width: '60%' }} />
                      </div>
                      <p className="text-[10px] text-center text-muted-foreground/60">Preparing press-ready PDF — please wait</p>
                    </div>
                  ) : preCompileState === "failed" ? (
                    <>
                      <div className="mb-3 p-2 bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 rounded-lg">
                        <p className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1.5">
                          <XCircle className="w-3.5 h-3.5 shrink-0" />
                          Pre-compilation failed. Click to retry.
                        </p>
                      </div>
                      <Button
                        className="w-full bg-green-600 hover:bg-green-700 text-white gap-2 shadow-md shadow-green-500/20 text-base py-5"
                        onClick={() => {
                          setPreCompileState(null);
                          handleCompilePressReady();
                        }}
                        data-testid="button-download-print-ready"
                      >
                        <Download className="w-5 h-5" /> Retry & Download
                      </Button>
                    </>
                  ) : (
                    <a
                      href={hasUserSelectedBleed ? `/api/jobs/${job.id}/download-bundle?strategy=${encodeURIComponent(selectedBleedMethod)}` : "#"}
                      download="Print Ready Artwork.zip"
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => {
                        if (!hasUserSelectedBleed) { e.preventDefault(); return; }
                        setHasDownloaded(true);
                      }}
                      className={`w-full bg-green-600 hover:bg-green-700 text-white gap-2 shadow-md shadow-green-500/20 text-base py-5 inline-flex items-center justify-center rounded-md font-medium transition-colors ${!hasUserSelectedBleed ? 'opacity-50 pointer-events-none' : ''}`}
                      data-testid="button-download-print-ready"
                    >
                      <Download className="w-5 h-5 mr-2" /> Download Print Ready Artwork
                    </a>
                  )}
                </Card>
              </motion.div>

              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.6 }}
                className="relative mt-6"
              >
                <div className="max-w-lg mx-auto p-5 bg-purple-50 dark:bg-purple-500/10 border border-purple-500/30 rounded-xl">
                  <Mail className="w-7 h-7 text-purple-600 mx-auto mb-3" />
                  <h3 className="text-base font-bold text-foreground mb-1 text-center">Share Report & Files</h3>
                  <p className="text-xs text-muted-foreground mb-4 text-center max-w-md mx-auto">
                    Enter your email address and we'll send your Artwork Intelligence Report and print-ready files to our sales team so they can assist you further.
                  </p>
                  {!shareComplete ? (
                    <>
                      <div className="flex gap-2 max-w-md mx-auto">
                        <Input
                          type="email"
                          placeholder="your@email.com"
                          value={shareEmail}
                          onChange={(e) => setShareEmail(e.target.value)}
                          className="flex-1"
                          data-testid="input-share-email"
                          disabled={isSharing}
                        />
                        <Button
                          onClick={async () => {
                            if (!shareEmail || !shareEmail.includes('@')) {
                              toast({ title: 'Please enter a valid email address', variant: 'destructive' });
                              return;
                            }
                            setIsSharing(true);
                            try {
                              const res = await apiRequest('POST', `/api/jobs/${job.id}/share`, { email: shareEmail });
                              const data = await res.json();
                              setShareComplete(true);
                              toast({ title: data.message || 'Shared successfully!' });
                            } catch (err: any) {
                              toast({ title: err.message || 'Failed to share', variant: 'destructive' });
                            } finally {
                              setIsSharing(false);
                            }
                          }}
                          disabled={isSharing || !shareEmail}
                          className="bg-purple-600 hover:bg-purple-700 text-white gap-2"
                          data-testid="button-share-report"
                        >
                          {isSharing ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Send className="w-4 h-4" />
                          )}
                          {isSharing ? 'Sending...' : 'Share'}
                        </Button>
                      </div>
                    </>
                  ) : (
                    <div className="text-center">
                      <CheckCircle2 className="w-8 h-8 text-green-500 mx-auto mb-2" />
                      <p className="text-sm font-medium text-green-700 dark:text-green-400">
                        Report shared successfully!
                      </p>
                    </div>
                  )}
                </div>
              </motion.div>

              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.7 }}
                className="relative mt-8 max-w-lg mx-auto text-center"
              >
                <p className="text-sm font-medium text-green-800 dark:text-green-300 leading-relaxed">
                  You've just powered through the most advanced artwork check in the industry. By choosing Flyerz.co.za Artwork Intelligence, you've moved beyond "guessing" and stepped into precision. Your design isn't just a file anymore — it's a high-performance asset, analyzed and optimized by an engine built for perfection. You've done the hard part; now sit back and watch your vision come to life with a clarity that standard systems simply can't match.
                </p>
                <p className="text-sm font-semibold text-green-900 dark:text-green-200 leading-relaxed mt-3">
                  Welcome to the new standard. Your journey to a perfect print starts now.
                </p>
              </motion.div>

              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.8 }}
                className="relative mt-4 text-center"
              >
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    handleBlobDownload(`/api/jobs/${job.id}/download/report`, `compliance_report_${job.id}.txt`, "tech-report");
                  }}
                  disabled={downloadingFile === "tech-report"}
                  className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2 cursor-pointer disabled:opacity-50"
                  data-testid="button-download-tech-report"
                >
                  {downloadingFile === "tech-report" ? "Downloading..." : "Download Technical Report (JSON)"}
                </button>
              </motion.div>
            </Card>

            {job.auditResults && (
              <PhaseChecklist checks={job.auditResults.checks} jobId={job.id} filename={job.filename} />
            )}
          </motion.div>
        )}

      </div>

      {guillotineOpen && currentBleedPage && (
        <GuillotineCutModal
          imageUrl={currentBleedPage.url}
          totalSize_mm={currentBleedPage.totalSize_mm}
          trimSize_mm={currentBleedPage.trimSize_mm}
          bleed_mm={currentBleedPage.bleed_mm}
          onClose={() => setGuillotineOpen(false)}
        />
      )}

    </Layout>
  );
}



function BleedPreviewPanel({ bleedPreview, bleedPreviewLoading, bleedPreviewError, bleedPreviewPage, setBleedPreviewPage, currentBleedPage, loadBleedPreview, enhancementLoading }: {
  bleedPreview: BleedPreviewData | null;
  bleedPreviewLoading: boolean;
  bleedPreviewError: string | null;
  bleedPreviewPage: number;
  setBleedPreviewPage: (p: number) => void;
  currentBleedPage: BleedPreviewPage | undefined;
  loadBleedPreview: () => void;
  enhancementLoading?: string | null;
}) {
  if (bleedPreviewLoading) {
    return (
      <div className="flex flex-col items-center gap-3 py-8">
        <Loader2 className="w-10 h-10 animate-spin text-primary" />
        <p className="text-sm font-medium">Generating bleed preview...</p>
      </div>
    );
  }

  if (bleedPreviewError) {
    return (
      <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-sm text-red-700 dark:text-red-400">
        <AlertCircle className="w-5 h-5 shrink-0" />
        <span>{bleedPreviewError}</span>
        <Button variant="ghost" size="sm" onClick={loadBleedPreview} className="ml-auto text-xs">Retry</Button>
      </div>
    );
  }

  if (!bleedPreview || bleedPreview.previewUrls.length === 0) {
    return (
      <div className="text-center py-8">
        <Scissors className="w-10 h-10 text-muted-foreground/40 mx-auto mb-3" />
        <p className="text-sm text-muted-foreground mb-3">Preview showing trim/cut lines and bleed boundaries.</p>
        <Button onClick={loadBleedPreview} className="hover-elevate" data-testid="button-generate-bleed-preview">
          <Eye className="w-4 h-4 mr-2" /> Generate Bleed View
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3 relative">
      {enhancementLoading && (
        <div className="absolute inset-0 z-10 bg-white/60 dark:bg-background/60 backdrop-blur-[2px] rounded-lg flex flex-col items-center justify-center gap-2" data-testid="enhancement-loading-overlay">
          <Loader2 className="w-6 h-6 animate-spin text-violet-500" />
          <p className="text-xs font-medium text-violet-600 dark:text-violet-400">Applying {enhancementLoading.replace(/_/g, ' ')}...</p>
          <div className="w-32 h-1 rounded-full bg-violet-100 dark:bg-violet-500/20 overflow-hidden">
            <div className="h-full bg-violet-500 rounded-full animate-pulse" style={{ width: '60%' }} />
          </div>
        </div>
      )}
      {bleedPreview.pageCount > 1 && (
        <div className="flex items-center gap-2 flex-wrap" data-testid="bleed-preview-page-selector">
          {bleedPreview.previewUrls.map((p, idx) => (
            <Button
              key={p.page}
              variant={bleedPreviewPage === idx ? "default" : "outline"}
              size="sm"
              className="text-xs"
              onClick={() => setBleedPreviewPage(idx)}
              data-testid={`button-bleed-page-${p.page}`}
            >
              Page {p.page}
            </Button>
          ))}
        </div>
      )}

      {currentBleedPage && (
        <>
          <div className="relative bg-gray-900 rounded-xl border-2 border-red-500/30 overflow-hidden" data-testid="bleed-preview-image-container">
            <img
              src={`${currentBleedPage.url}?t=${Date.now()}`}
              alt={`Bleed Preview - Page ${currentBleedPage.page}`}
              className="w-full h-auto max-h-[500px] object-contain"
              data-testid="img-bleed-preview"
            />
            {bleedPreviewLoading && (
              <div className="absolute inset-0 bg-black/40 flex flex-col items-center justify-center gap-2" data-testid="overlay-bleed-processing">
                <Loader2 className="w-8 h-8 animate-spin text-white" />
                <span className="text-sm font-medium text-white/90">Generating preview...</span>
              </div>
            )}
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className="bg-muted/40 rounded-lg p-2.5 border border-border/40 text-center">
              <div className="text-[10px] text-muted-foreground mb-0.5">Trim Size</div>
              <div className="text-xs font-mono font-bold text-foreground" data-testid="text-trim-size">
                {currentBleedPage.trimSize_mm[0]} × {currentBleedPage.trimSize_mm[1]}mm
              </div>
            </div>
            <div className="bg-muted/40 rounded-lg p-2.5 border border-border/40 text-center">
              <div className="text-[10px] text-muted-foreground mb-0.5">Total Size</div>
              <div className="text-xs font-mono font-bold text-foreground" data-testid="text-total-size">
                {currentBleedPage.totalSize_mm[0]} × {currentBleedPage.totalSize_mm[1]}mm
              </div>
            </div>
            <div className="bg-muted/40 rounded-lg p-2.5 border border-border/40 text-center">
              <div className="text-[10px] text-muted-foreground mb-0.5">Bleed</div>
              <div className="text-xs font-mono font-bold text-foreground" data-testid="text-bleed-amount">
                {currentBleedPage.bleed_mm}mm
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3 text-[10px] text-muted-foreground justify-center">
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 bg-red-500 inline-block rounded"></span> Cut line
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 bg-green-500 inline-block rounded border-dashed border-t border-green-500"></span> Safe zone
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-2 bg-red-500/25 inline-block rounded"></span> Bleed area
            </span>
          </div>
        </>
      )}
    </div>
  );
}


const AUDIT_CATEGORIES: { key: string; emoji: string; label: string; patterns: RegExp[] }[] = [
  {
    key: "canvas",
    emoji: "📐",
    label: "Canvas & Bleed",
    patterns: [
      /bleed/i, /trim/i, /canvas/i, /page/i, /margin/i, /safe.?zone/i,
      /white.?edge/i, /spine/i, /gutter/i, /creep/i, /downscale/i,
      /layout/i, /composition/i, /geometry/i, /content.?margins/i,
    ],
  },
  {
    key: "typography",
    emoji: "🖋️",
    label: "Typography",
    patterns: [
      /font/i, /text/i, /typo/i, /small.?text/i, /k.?only/i,
      /neutrali[sz]/i, /overprint/i,
    ],
  },
  {
    key: "ink",
    emoji: "🎨",
    label: "Ink Chemistry",
    patterns: [
      /cmyk/i, /color.?space/i, /colour/i, /ink/i, /tic/i,
      /rich.?black/i, /pdf.?x/i, /compliance/i, /visual.?proof/i,
      /sign.?off/i, /comparison/i,
    ],
  },
  {
    key: "resolution",
    emoji: "🔍",
    label: "Resolution",
    patterns: [
      /dpi/i, /resolution/i, /upscal/i, /enhance/i, /raster/i,
      /flatten/i, /lens/i, /transparen/i, /shadow/i, /image/i,
      /complex/i, /embed/i, /format/i, /print.?read/i, /input.?standard/i,
      /emergency/i, /processing/i, /readiness/i,
    ],
  },
];

function categorizeChecks(checks: any[]) {
  const grouped: Record<string, any[]> = { canvas: [], typography: [], ink: [], resolution: [] };
  const assigned = new Set<number>();

  for (const cat of AUDIT_CATEGORIES) {
    checks.forEach((check, idx) => {
      if (assigned.has(idx)) return;
      const haystack = `${check.name} ${check.message || ""}`;
      if (cat.patterns.some(p => p.test(haystack))) {
        grouped[cat.key].push(check);
        assigned.add(idx);
      }
    });
  }
  checks.forEach((check, idx) => {
    if (!assigned.has(idx)) {
      grouped.resolution.push(check);
    }
  });
  return grouped;
}

function categoryBorderColor(items: any[]): string {
  if (items.some(c => !c.passed && !c.autoFixed)) return "#ef4444";
  if (items.some(c => c.autoFixed)) return "#fbbf24";
  return "#4ade80";
}

function categorySummary(items: any[]): string {
  if (items.length === 0) return "No checks";
  const allPassed = items.every(c => c.passed);
  const anyFail = items.some(c => !c.passed && !c.autoFixed);
  if (allPassed) return "All clear";
  if (anyFail) return `${items.filter(c => !c.passed && !c.autoFixed).length} issue(s)`;
  return `${items.filter(c => c.autoFixed).length} auto-fixed`;
}

function PhaseChecklist({ checks, jobId, filename }: { checks: any[]; jobId?: number; filename?: string }) {
  const [expanded, setExpanded] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [scanIndex, setScanIndex] = useState(0);
  const passedCount = checks.filter(c => c.passed).length;
  const grouped = categorizeChecks(checks);

  useEffect(() => {
    if (!expanded) {
      setRevealed(false);
      setScanIndex(0);
      return;
    }
    const scanSteps = AUDIT_CATEGORIES.length;
    const stepDelay = 500;
    let step = 0;
    const scanTimer = setInterval(() => {
      step++;
      setScanIndex(step);
      if (step >= scanSteps) {
        clearInterval(scanTimer);
        setTimeout(() => setRevealed(true), 500);
      }
    }, stepDelay);
    return () => clearInterval(scanTimer);
  }, [expanded]);

  return (
    <div className="audit-container mb-6" data-testid="audit-dashboard">
      <button
        className="w-full flex items-center justify-between mb-4"
        onClick={() => setExpanded(!expanded)}
        data-testid="button-toggle-checklist"
      >
        <div className="flex items-center gap-3">
          <FileCheck className="w-5 h-5" style={{ color: "#4ade80" }} />
          <span className="text-sm font-bold" style={{ color: "#e6e6fa" }}>Flyerz.co.za Artwork Intelligence Report</span>
          <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: "rgba(74, 222, 128, 0.15)", color: "#4ade80" }}>
            {passedCount}/{checks.length} passed
          </span>
        </div>
        <ChevronRight className={`w-4 h-4 transition-transform ${expanded ? 'rotate-90' : ''}`} style={{ color: "#b8c1ec" }} />
      </button>
      {jobId != null && jobId > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <a
            href={`/api/jobs/${jobId}/intelligence-report`}
            download={filename ? `Flyerz_Intelligence_Report_${filename.replace(/[^\w.\-]+/g, "_")}.pdf` : undefined}
            className="inline-flex items-center gap-2 text-xs font-semibold px-3 py-2 rounded-lg border border-[#334155] text-[#e6e6fa] hover:bg-white/5 transition-colors"
            data-testid="link-intelligence-report-pdf"
          >
            <Download className="w-3.5 h-3.5" />
            Download Intelligence Report PDF
          </a>
          <span className="text-[11px] text-[#8891b0]">Includes job telemetry; Auto-Heal badge if Shrink &amp; Re-Bleed ran.</span>
        </div>
      )}

      {expanded && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          className="audit-grid"
        >
          {AUDIT_CATEGORIES.map((cat, catIdx) => {
            const items = grouped[cat.key];
            const isScanning = !revealed && scanIndex <= catIdx;
            const isScanned = !revealed && scanIndex > catIdx;
            return (
              <div
                key={cat.key}
                className={`audit-card ${!revealed ? "audit-card-scanning" : ""}`}
                data-testid={`audit-category-${cat.key}`}
                style={{ borderLeftColor: revealed ? (items.length > 0 ? categoryBorderColor(items) : "#0f3460") : isScanned ? "#4ade80" : "#0f3460" }}
              >
                <h3>{cat.emoji} {cat.label}</h3>
                {!revealed ? (
                  <p className="audit-scan-status" data-testid={`audit-scan-${cat.key}`}>
                    {isScanning ? (
                      <span className="audit-scanning-text">
                        <Loader2 className="w-3.5 h-3.5 animate-spin inline-block mr-1.5" style={{ color: "#b8c1ec" }} />
                        Scanning...
                      </span>
                    ) : (
                      <span style={{ color: "#4ade80" }}>
                        <CheckCircle2 className="w-3.5 h-3.5 inline-block mr-1.5" style={{ verticalAlign: "text-bottom" }} />
                        Scanned
                      </span>
                    )}
                  </p>
                ) : (
                  <>
                    <p className="audit-category-summary" data-testid={`audit-summary-${cat.key}`}>{categorySummary(items)}</p>
                    {items.length > 0 && (
                      <ul className="audit-check-list">
                        {items.map((check, i) => (
                          <li key={i} className="audit-check-item" data-testid={`check-item-${cat.key}-${i}`}>
                            <div className="flex items-start gap-2">
                              <div className="shrink-0 mt-0.5">
                                {check.passed ? (
                                  <CheckCircle2 className="w-3.5 h-3.5" style={{ color: "#4ade80" }} />
                                ) : check.autoFixed ? (
                                  <Wand2 className="w-3.5 h-3.5" style={{ color: "#fbbf24" }} />
                                ) : (
                                  <XCircle className="w-3.5 h-3.5" style={{ color: "#ef4444" }} />
                                )}
                              </div>
                              <div className="flex-1 min-w-0">
                                <span className="audit-check-name">{check.name}</span>
                                <span className="audit-check-msg">{(() => {
                                  if (check.name === "Safe Zone Validation" && !check.passed) {
                                    const distMatch = (check.details || "").match(/(\d+\.?\d*)mm/);
                                    const dist = distMatch ? parseFloat(distMatch[1]) : 999;
                                    if (dist > 1.0) {
                                      return `Content detected ${dist}mm from trim edge — within industry tolerance. No action required.`;
                                    }
                                  }
                                  return check.message;
                                })()}</span>
                                {(check as any).cmykVerified && (
                                  <div className="audit-badge-pass mt-1" data-testid="badge-cmyk-verified">
                                    <ShieldCheck className="w-3 h-3" />
                                    CMYK Verified
                                  </div>
                                )}
                                {check.severity && check.severity !== "PASS" && (() => {
                                  let effectiveSeverity = check.severity;
                                  if (check.name === "Safe Zone Validation" && (check.severity === "CRITICAL" || check.severity === "HIGH")) {
                                    const distMatch = (check.details || "").match(/(\d+\.?\d*)mm/);
                                    const dist = distMatch ? parseFloat(distMatch[1]) : 999;
                                    if (dist > 1.0) effectiveSeverity = "INFO";
                                  }
                                  if (effectiveSeverity === "INFO") return null;
                                  return (
                                    <div className={`mt-1 ${
                                      effectiveSeverity === "CRITICAL" || effectiveSeverity === "HIGH" ? "audit-badge-fail" : "audit-badge-fixed"
                                    }`} data-testid={`badge-severity-${cat.key}-${i}`}>
                                      {effectiveSeverity === "CRITICAL" ? "Critical" :
                                       effectiveSeverity === "HIGH" ? "High Risk" :
                                       effectiveSeverity === "MANUAL_REVIEW" ? "Manual Review" :
                                       "Warning"}
                                    </div>
                                  );
                                })()}
                                {check.details && (
                                  <p className="audit-check-details">{check.details}</p>
                                )}
                              </div>
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
              </div>
            );
          })}
        </motion.div>
      )}
    </div>
  );
}

function BleedMethodSelector({ jobId, variants, recommended, selected, onSelect, loading }: {
  jobId: number;
  variants: Record<string, string>;
  recommended: string | null;
  selected: string;
  onSelect: (method: string) => void;
  loading: boolean;
}) {
  /** Always list every registered strategy; variant paths may be partial if generation skipped a tile. */
  const methods = [...BLEED_STRATEGY_IDS];
  const activeMethod = selected === "auto" ? recommended : selected;
  const activeInfo = activeMethod ? BLEED_METHOD_LABELS[activeMethod as keyof typeof BLEED_METHOD_LABELS] : null;

  if (methods.length === 0) return null;

  return (
    <div data-testid="section-bleed-method-selector">
      <div className="flex items-center gap-2 mb-3">
        <Sparkles className="w-4 h-4 text-primary" />
        <h4 className="text-sm font-bold text-foreground">Choose Your Bleed Style</h4>
        {loading && (
          <div className="flex items-center gap-2 ml-auto">
            <Loader2 className="w-4 h-4 animate-spin text-primary" />
            <span className="text-xs font-medium text-primary animate-pulse" data-testid="text-bleed-processing">Processing...</span>
          </div>
        )}
      </div>
      <div className="flex flex-col gap-3">
        <select
          value={activeMethod || ""}
          onChange={(e) => onSelect(e.target.value)}
          disabled={loading}
          className="w-full px-3 py-2.5 rounded-lg border-2 border-border/60 bg-white dark:bg-background text-sm font-medium text-foreground focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all disabled:opacity-50 disabled:cursor-not-allowed appearance-none cursor-pointer"
          style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='none' viewBox='0 0 24 24' stroke='%236b7280' stroke-width='2'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E")`, backgroundRepeat: "no-repeat", backgroundPosition: "right 12px center" }}
          data-testid="select-bleed-method"
        >
          {methods.map((method) => {
            const info = BLEED_METHOD_LABELS[method as keyof typeof BLEED_METHOD_LABELS];
            const isRecommended = method === recommended;
            return (
              <option key={method} value={method} data-testid={`option-bleed-method-${method}`}>
                {info?.label ?? method}{isRecommended ? " (Recommended)" : ""}
              </option>
            );
          })}
        </select>
        {activeInfo && (
          <p className="text-xs text-muted-foreground" data-testid="text-bleed-description">
            {activeInfo.description}
          </p>
        )}
        {activeMethod && (
          <div className={`relative rounded-lg border-2 border-primary/30 overflow-hidden bg-gray-100 dark:bg-gray-800 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
            <div className="aspect-[16/9]">
              {variants[activeMethod] ? (
              <img
                src={`/api/jobs/${jobId}/bleed-variant/${activeMethod}`}
                alt={activeInfo?.label || activeMethod}
                className="w-full h-full object-contain"
                data-testid={`img-bleed-variant-${activeMethod}`}
              />
              ) : (
                <div
                  className="w-full h-full flex items-center justify-center px-4 text-center text-xs text-muted-foreground"
                  data-testid={`placeholder-bleed-variant-${activeMethod}`}
                >
                  No cached preview for this strategy — select &quot;Apply&quot; or re-run processing to generate it.
                </div>
              )}
              {loading && (
                <div className="absolute inset-0 bg-black/30 flex items-center justify-center" data-testid="overlay-loading-bleed">
                  <Loader2 className="w-6 h-6 animate-spin text-white" />
                </div>
              )}
            </div>
            <div className="absolute top-2 left-2 flex items-center gap-1 px-2 py-1 rounded-full bg-primary/90 text-primary-foreground text-[10px] font-bold">
              <CheckCircle2 className="w-3 h-3" />
              {activeInfo?.label || activeMethod}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function GuillotineCutModal({ imageUrl, totalSize_mm, trimSize_mm, bleed_mm, onClose }: {
  imageUrl: string;
  totalSize_mm: [number, number];
  trimSize_mm: [number, number];
  bleed_mm: number;
  onClose: () => void;
}) {
  const [sliderValue, setSliderValue] = useState(0);

  return (
    <div style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.95)', zIndex: 9999, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'space-between', padding: '20px' }} data-testid="modal-guillotine">

      <div style={{ width: '100%', display: 'flex', justifyContent: 'flex-end', paddingBottom: '10px' }}>
        <button onClick={onClose} style={{ backgroundColor: '#dc2626', color: 'white', padding: '10px 20px', borderRadius: '6px', fontWeight: 'bold', border: 'none', cursor: 'pointer' }} data-testid="button-guillotine-close">
          CLOSE X
        </button>
      </div>

      <div style={{ flex: 1, width: '100%', maxHeight: '65vh', display: 'flex', justifyContent: 'center', alignItems: 'center', perspective: '1000px' }}>
        <img
          src={imageUrl}
          alt="3D Proof"
          style={{
            maxHeight: '100%',
            maxWidth: '100%',
            objectFit: 'contain',
            transform: 'rotateX(15deg) rotateY(-5deg)',
            boxShadow: '0 30px 60px rgba(0,0,0,0.7)',
            clipPath: `inset(${sliderValue}% ${sliderValue}% ${sliderValue}% ${sliderValue}%)`,
            transition: 'clip-path 0.1s ease-out'
          }}
          data-testid="guillotine-trimmed-image"
        />
      </div>

      <div style={{ width: '100%', maxWidth: '400px', backgroundColor: 'white', padding: '20px', borderRadius: '10px', marginBottom: '2vh', marginTop: '20px' }} data-testid="section-guillotine-controls">
        <p style={{ fontWeight: 'bold', textAlign: 'center', margin: '0 0 10px 0', color: '#111827' }}>Simulate Guillotine Cut</p>
        <input
          type="range"
          min="0"
          max="6"
          step="0.1"
          value={sliderValue}
          onChange={(e) => setSliderValue(parseFloat(e.target.value))}
          style={{ width: '100%', cursor: 'pointer' }}
          data-testid="slider-guillotine"
        />
        <div style={{ width: '100%', display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: '#6b7280', marginTop: '6px' }}>
          <span>Raw Edge</span>
          <span>Final Trim</span>
        </div>
      </div>
    </div>
  );
}
