import { useCallback, useState, useEffect } from "react";
import { useDropzone } from "react-dropzone";
import { useLocation } from "wouter";
import { useMutation } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import {
  UploadCloud, AlertCircle, Loader2, CheckCircle2, XCircle,
  Wrench, Wand2, ChevronDown, ChevronUp, Search, Ruler
} from "lucide-react";
import { defaultBleedOptions } from "@shared/schema";

interface QuickCheckItem {
  id: string;
  name: string;
  passed: boolean;
  message: string;
  details?: string;
  fixType: "auto" | "manual";
  severity: string;
}

interface ArtworkSize {
  width_mm: number;
  height_mm: number;
  has_bleed: boolean;
  bleed_mm: { top: number; bottom: number; left: number; right: number };
  document_width_mm: number;
  document_height_mm: number;
}

interface QuickCheckResult {
  checks: QuickCheckItem[];
  allPassed: boolean;
  passCount: number;
  failCount: number;
  storedFilename: string;
  originalFilename: string;
  fileType: string;
  artworkSize: ArtworkSize;
}

export function QuickCheck() {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const [result, setResult] = useState<QuickCheckResult | null>(null);
  const [expandedCheck, setExpandedCheck] = useState<string | null>(null);
  const [fixingItems, setFixingItems] = useState<Set<string>>(new Set());

  const [targetWidth, setTargetWidth] = useState("");
  const [targetHeight, setTargetHeight] = useState("");
  const [detectedWidth, setDetectedWidth] = useState<number | null>(null);
  const [detectedHeight, setDetectedHeight] = useState<number | null>(null);

  useEffect(() => {
    if (result?.artworkSize) {
      const { width_mm, height_mm } = result.artworkSize;
      setDetectedWidth(width_mm);
      setDetectedHeight(height_mm);
      if (!targetWidth) setTargetWidth(width_mm.toString());
      if (!targetHeight) setTargetHeight(height_mm.toString());
    }
  }, [result]);

  const quickCheckMutation = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch("/api/quick-check", {
        method: "POST",
        body: formData,
        credentials: "include",
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.message || "Quick check failed");
      }
      return res.json() as Promise<QuickCheckResult>;
    },
    onSuccess: (data) => {
      setResult(data);
      if (data.allPassed) {
        toast({ title: "All checks passed!", description: "Your artwork is print-ready." });
      } else {
        toast({
          title: `${data.failCount} issue(s) found`,
          description: "Review the results below and fix individually or automate all fixes.",
          variant: "destructive",
        });
      }
    },
    onError: (error: Error) => {
      toast({ title: "Check failed", description: error.message, variant: "destructive" });
    },
  });

  const fixMutation = useMutation({
    mutationFn: async (params: { storedFilename: string; originalFilename: string; fileType: string }) => {
      const res = await fetch("/api/quick-check/fix", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...params,
          bleedOptions: defaultBleedOptions,
        }),
        credentials: "include",
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.message || "Fix failed");
      }
      return res.json() as Promise<{ jobId: number; filename: string; status: string }>;
    },
    onSuccess: (data) => {
      toast({ title: "Fix started", description: "Redirecting to job details..." });
      setLocation(`/job/${data.jobId}`);
    },
    onError: (error: Error) => {
      toast({ title: "Fix failed", description: error.message, variant: "destructive" });
    },
  });

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    const file = acceptedFiles[0];
    if (!file) return;

    if (file.size > 50 * 1024 * 1024) {
      toast({ title: "File too large", description: "Max 50MB.", variant: "destructive" });
      return;
    }

    setResult(null);
    setExpandedCheck(null);
    setFixingItems(new Set());
    setDetectedWidth(null);
    setDetectedHeight(null);
    quickCheckMutation.mutate(file);
  }, [quickCheckMutation, toast]);

  const { getRootProps, getInputProps, isDragActive, isDragReject } = useDropzone({
    onDrop,
    maxFiles: 1,
    accept: {
      "application/pdf": [".pdf"],
      "image/jpeg": [".jpg", ".jpeg"],
      "image/png": [".png"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
      "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
    },
  });

  const handleAutoFixSingle = (checkId: string) => {
    if (!result) return;
    setFixingItems(prev => new Set(prev).add(checkId));
    fixMutation.mutate({
      storedFilename: result.storedFilename,
      originalFilename: result.originalFilename,
      fileType: result.fileType,
    });
  };

  const handleAutomateAll = () => {
    if (!result) return;
    fixMutation.mutate({
      storedFilename: result.storedFilename,
      originalFilename: result.originalFilename,
      fileType: result.fileType,
    });
  };

  const handleReset = () => {
    setResult(null);
    setExpandedCheck(null);
    setFixingItems(new Set());
    setTargetWidth("");
    setTargetHeight("");
    setDetectedWidth(null);
    setDetectedHeight(null);
  };

  const isProcessing = quickCheckMutation.isPending;
  const isFixing = fixMutation.isPending;

  const manualToolForCheck: Record<string, { label: string; path: string }> = {
    bleed: { label: "Safe Margin Shrink", path: "/shrink" },
    resolution: { label: "Precision Resizer", path: "/" },
    print_ready: { label: "Manual Crop & Downscale", path: "/crop" },
    cmyk: { label: "Precision Resizer (CMYK)", path: "/" },
    transparency: { label: "Manual Crop & Downscale", path: "/crop" },
    artwork_size: { label: "Precision Resizer", path: "/" },
  };

  const targetW = parseFloat(targetWidth);
  const targetH = parseFloat(targetHeight);

  const sizeCheck: QuickCheckItem | null = (() => {
    if (!result || !detectedWidth || !detectedHeight) return null;
    if (!targetW || !targetH || targetW <= 0 || targetH <= 0) return null;

    const wDiff = Math.abs(detectedWidth - targetW);
    const hDiff = Math.abs(detectedHeight - targetH);
    const tolerance = 1.0;

    if (wDiff <= tolerance && hDiff <= tolerance) {
      return {
        id: "artwork_size",
        name: "Artwork Size",
        passed: true,
        message: `Artwork is ${detectedWidth} x ${detectedHeight}mm — matches required ${targetW} x ${targetH}mm`,
        details: result.artworkSize.has_bleed
          ? `Trim size (excluding bleed): ${detectedWidth} x ${detectedHeight}mm. Document with bleed: ${result.artworkSize.document_width_mm} x ${result.artworkSize.document_height_mm}mm.`
          : `Content size: ${detectedWidth} x ${detectedHeight}mm. Document: ${result.artworkSize.document_width_mm} x ${result.artworkSize.document_height_mm}mm.`,
        fixType: "auto" as const,
        severity: "PASS",
      };
    } else {
      return {
        id: "artwork_size",
        name: "Artwork Size",
        passed: false,
        message: `Size mismatch: artwork is ${detectedWidth} x ${detectedHeight}mm but you need ${targetW} x ${targetH}mm`,
        details: `Detected trim size: ${detectedWidth} x ${detectedHeight}mm (excluding bleed). Required: ${targetW} x ${targetH}mm. Difference: ${wDiff.toFixed(1)}mm width, ${hDiff.toFixed(1)}mm height.${result.artworkSize.has_bleed ? ` Document with bleed: ${result.artworkSize.document_width_mm} x ${result.artworkSize.document_height_mm}mm. Bleed is excluded from this measurement so resizing won't shrink your actual artwork.` : " No bleed detected on this file."}`,
        fixType: "auto" as const,
        severity: "HIGH",
      };
    }
  })();

  const allChecks = result ? [...result.checks, ...(sizeCheck ? [sizeCheck] : [])] : [];
  const effectiveFailCount = allChecks.filter(c => !c.passed).length;
  const effectivePassCount = allChecks.filter(c => c.passed).length;
  const effectiveAllPassed = effectiveFailCount === 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 mb-1">
        <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary/10 text-primary font-bold text-sm shrink-0">1</div>
        <div>
          <h2 className="text-lg font-bold text-foreground" data-testid="text-step1-title">Quick Pre-flight Check</h2>
          <p className="text-xs text-muted-foreground">Drop your artwork to instantly check litho-readiness — 5mm bleed, CMYK, DPI, transparency, size, and more</p>
        </div>
      </div>

      <Card className="p-4 border border-border/60" data-testid="card-print-size">
        <div className="flex items-center gap-2 mb-3">
          <Ruler className="w-4 h-4 text-primary" />
          <Label className="text-sm font-semibold text-foreground">Required Print Size (mm)</Label>
          {result?.artworkSize?.has_bleed && (
            <span className="text-[10px] font-medium bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400 px-2 py-0.5 rounded-full">Bleed detected — showing trim size only</span>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="quick-width" className="text-xs text-muted-foreground">Width (mm)</Label>
            <Input
              id="quick-width"
              type="number"
              min="1"
              max="3000"
              step="0.1"
              placeholder={detectedWidth ? `Detected: ${detectedWidth}` : "e.g. 148"}
              value={targetWidth}
              onChange={(e) => setTargetWidth(e.target.value)}
              data-testid="input-target-width"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="quick-height" className="text-xs text-muted-foreground">Height (mm)</Label>
            <Input
              id="quick-height"
              type="number"
              min="1"
              max="3000"
              step="0.1"
              placeholder={detectedHeight ? `Detected: ${detectedHeight}` : "e.g. 210"}
              value={targetHeight}
              onChange={(e) => setTargetHeight(e.target.value)}
              data-testid="input-target-height"
            />
          </div>
        </div>
        {detectedWidth && detectedHeight && (
          <p className="text-xs text-muted-foreground mt-2" data-testid="text-detected-size">
            Detected artwork size: <span className="font-semibold text-foreground">{detectedWidth} x {detectedHeight}mm</span>
            {result?.artworkSize?.has_bleed && (
              <span> (trim only — full document with bleed: {result.artworkSize.document_width_mm} x {result.artworkSize.document_height_mm}mm)</span>
            )}
          </p>
        )}
      </Card>

      {!result && (
        <Card
          className={`relative overflow-hidden group cursor-pointer transition-all duration-300 border-2 border-dashed bg-card hover-elevate ${
            isDragActive ? "border-primary bg-primary/5" :
            isDragReject ? "border-destructive bg-destructive/5" :
            "border-border/60 hover:border-primary/50"
          }`}
          {...getRootProps()}
          data-testid="dropzone-quick-check"
        >
          <input {...getInputProps()} data-testid="input-quick-check" />
          <div className="absolute inset-0 bg-grid-pattern opacity-[0.2] pointer-events-none mix-blend-overlay" />
          <div className="relative z-10 flex flex-col items-center justify-center p-10 text-center min-h-[200px]">
            <div className={`w-16 h-16 rounded-full flex items-center justify-center mb-4 transition-all duration-500 shadow-lg ${
              isProcessing ? "bg-primary text-primary-foreground animate-pulse" :
              isDragActive ? "bg-primary text-primary-foreground scale-110" :
              "bg-primary/10 text-primary group-hover:scale-110 group-hover:bg-primary group-hover:text-primary-foreground"
            }`}>
              {isProcessing ? <Loader2 className="w-8 h-8 animate-spin" /> :
               isDragActive ? <UploadCloud className="w-8 h-8" /> :
               <Search className="w-8 h-8" />}
            </div>
            <h3 className="text-xl font-bold font-display tracking-tight text-foreground mb-1">
              {isProcessing ? "Scanning artwork..." : isDragActive ? "Drop to scan" : "Drop artwork to check"}
            </h3>
            <p className="text-muted-foreground text-sm max-w-sm">
              {isProcessing ? "Checking bleed, CMYK, DPI, transparency, and print readiness..." :
               "Instant read-only scan — your file won't be modified"}
            </p>
            {!isProcessing && (
              <div className="mt-5 flex gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
                <span className="bg-muted px-2 py-1 rounded">PDF</span>
                <span className="bg-muted px-2 py-1 rounded">JPG/PNG</span>
                <span className="bg-muted px-2 py-1 rounded">DOCX/PPTX</span>
              </div>
            )}
          </div>
        </Card>
      )}

      <AnimatePresence>
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="space-y-3"
          >
            <Card className={`p-4 flex items-center gap-3 ${
              effectiveAllPassed
                ? "border-green-500/30 bg-green-50/50 dark:bg-green-950/20"
                : "border-amber-500/30 bg-amber-50/50 dark:bg-amber-950/20"
            }`} data-testid="card-quick-check-summary">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center shrink-0 ${
                effectiveAllPassed ? "bg-green-500/20 text-green-600" : "bg-amber-500/20 text-amber-600"
              }`}>
                {effectiveAllPassed ? <CheckCircle2 className="w-5 h-5" /> : <AlertCircle className="w-5 h-5" />}
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-sm text-foreground" data-testid="text-quick-summary">
                  {effectiveAllPassed
                    ? "All checks passed — artwork is print-ready!"
                    : `${effectiveFailCount} of ${allChecks.length} checks need attention`}
                </p>
                <p className="text-xs text-muted-foreground">
                  {result.originalFilename} — {effectivePassCount} passed, {effectiveFailCount} failed
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={handleReset} data-testid="button-scan-new">
                Scan Another
              </Button>
            </Card>

            <div className="space-y-2">
              {allChecks.map((check) => {
                const isExpanded = expandedCheck === check.id;
                const isFailing = !check.passed;
                const tool = manualToolForCheck[check.id];

                return (
                  <Card
                    key={check.id}
                    className={`overflow-hidden transition-all duration-200 ${
                      check.passed
                        ? "border-green-500/20 bg-green-50/30 dark:bg-green-500/5"
                        : "border-red-500/20 bg-red-50/30 dark:bg-red-500/5"
                    }`}
                    data-testid={`card-check-${check.id}`}
                  >
                    <button
                      className="w-full flex items-center gap-3 p-4 text-left hover:bg-muted/20 transition-colors"
                      onClick={() => setExpandedCheck(isExpanded ? null : check.id)}
                      data-testid={`button-expand-${check.id}`}
                    >
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
                        check.passed ? "bg-green-500/20" : "bg-red-500/20"
                      }`}>
                        {check.passed
                          ? <CheckCircle2 className="w-4 h-4 text-green-600" />
                          : <XCircle className="w-4 h-4 text-red-600" />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="font-semibold text-sm text-foreground">{check.name}</p>
                        <p className="text-xs text-muted-foreground truncate">{check.message}</p>
                      </div>
                      {isFailing && (
                        <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded-full shrink-0 ${
                          check.severity === "CRITICAL"
                            ? "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400"
                            : "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400"
                        }`}>
                          {check.severity}
                        </span>
                      )}
                      {isExpanded ? <ChevronUp className="w-4 h-4 text-muted-foreground shrink-0" /> : <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0" />}
                    </button>

                    <AnimatePresence>
                      {isExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          className="overflow-hidden"
                        >
                          <div className="px-4 pb-4 pt-0 border-t border-border/30">
                            {check.details && (
                              <p className="text-xs text-muted-foreground mt-3 mb-3 bg-muted/50 p-3 rounded-lg font-mono leading-relaxed" data-testid={`text-details-${check.id}`}>
                                {check.details}
                              </p>
                            )}
                            {isFailing && (
                              <div className="flex flex-wrap gap-2 mt-2">
                                {tool && (
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="text-xs gap-1.5"
                                    onClick={(e) => { e.stopPropagation(); setLocation(tool.path); }}
                                    data-testid={`button-manual-fix-${check.id}`}
                                  >
                                    <Wrench className="w-3.5 h-3.5" />
                                    Fix with {tool.label}
                                  </Button>
                                )}
                                <Button
                                  size="sm"
                                  className="text-xs gap-1.5"
                                  onClick={(e) => { e.stopPropagation(); handleAutoFixSingle(check.id); }}
                                  disabled={isFixing}
                                  data-testid={`button-auto-fix-${check.id}`}
                                >
                                  {fixingItems.has(check.id) ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wand2 className="w-3.5 h-3.5" />}
                                  Automate Fix
                                </Button>
                              </div>
                            )}
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </Card>
                );
              })}
            </div>

            {!effectiveAllPassed && (
              <Button
                className="w-full gap-2 h-12 text-base font-semibold"
                size="lg"
                onClick={handleAutomateAll}
                disabled={isFixing}
                data-testid="button-automate-all-fixes"
              >
                {isFixing ? <Loader2 className="w-5 h-5 animate-spin" /> : <Wand2 className="w-5 h-5" />}
                Automate All Fixes — Get Print-Ready Artwork
              </Button>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
