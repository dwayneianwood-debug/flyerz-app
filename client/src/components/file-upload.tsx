import { useCallback, useState, useEffect, useMemo, useRef } from "react";
import { useDropzone } from "react-dropzone";
import { UploadCloud, File, AlertCircle, Loader2, Settings2, ChevronDown, ChevronUp, Palette, Scissors, Layers, Grid3X3, Maximize2, Printer, Eye, Shield, BookOpen, Target, Move, Crosshair, Ruler, RectangleVertical, RectangleHorizontal, ImageIcon, X, CheckCircle2, XCircle, Clock, Crop } from "lucide-react";
import { useUploadJob } from "@/hooks/use-jobs";
import { useLocation } from "wouter";
import { Card } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { useToast } from "@/hooks/use-toast";
import type { BleedOptions } from "@shared/schema";
import { defaultBleedOptions } from "@shared/schema";
import { ManualCropEmbedded, type CropCoordinates } from "@/pages/manual-crop";
import { useBeta } from "@/lib/beta-flag";
import { optimizeImageViaWorker } from "@/lib/optimize-worker-client";
import { FULL_PAGE_CROP_BOX, isFullPageCropBox } from "@/lib/full-page-crop";

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const A0_W = 841;
const A0_H = 1189;

const KNOWN_SIZES: { key: string; label: string; w: number; h: number }[] = [
  { key: "a0", label: "A0", w: 841, h: 1189 },
  { key: "a1", label: "A1", w: 594, h: 841 },
  { key: "a2", label: "A2", w: 420, h: 594 },
  { key: "a3", label: "A3", w: 297, h: 420 },
  { key: "a4", label: "A4", w: 210, h: 297 },
  { key: "a5", label: "A5", w: 148, h: 210 },
  { key: "a6", label: "A6", w: 105, h: 148 },
  { key: "business-card", label: "Card", w: 90, h: 50 },
];

function detectFileDimensions(file: globalThis.File): Promise<{ w: number; h: number; pxW: number; pxH: number } | null> {
  return new Promise((resolve) => {
    if (file.type.startsWith("image/")) {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = () => {
        const dpi = 300;
        const wMm = Math.round((img.naturalWidth / dpi) * 25.4);
        const hMm = Math.round((img.naturalHeight / dpi) * 25.4);
        URL.revokeObjectURL(url);
        resolve({ w: wMm, h: hMm, pxW: img.naturalWidth, pxH: img.naturalHeight });
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(null);
      };
      img.src = url;
    } else {
      resolve(null);
    }
  });
}

function SizeComparator({ targetW, targetH, originalW, originalH }: {
  targetW: number | null;
  targetH: number | null;
  originalW: number | null;
  originalH: number | null;
}) {
  const CANVAS_W = 280;
  const CANVAS_H = 320;
  const PADDING = 24;

  const innerW = CANVAS_W - PADDING * 2;
  const innerH = CANVAS_H - PADDING * 2;

  const a0Scale = Math.min(innerW / A0_W, innerH / A0_H);
  const a0DispW = A0_W * a0Scale;
  const a0DispH = A0_H * a0Scale;

  const a0X = (CANVAS_W - a0DispW) / 2;
  const a0Y = (CANVAS_H - a0DispH) / 2;

  const hasTarget = targetW && targetH && targetW > 0 && targetH > 0;
  const hasOriginal = originalW && originalH && originalW > 0 && originalH > 0;

  const targetDispW = hasTarget ? targetW * a0Scale : 0;
  const targetDispH = hasTarget ? targetH * a0Scale : 0;
  const targetX = hasTarget ? a0X + (a0DispW - targetDispW) / 2 : 0;
  const targetY = hasTarget ? a0Y + (a0DispH - targetDispH) / 2 : 0;

  const origDispW = hasOriginal ? originalW * a0Scale : 0;
  const origDispH = hasOriginal ? originalH * a0Scale : 0;
  const origX = hasOriginal ? a0X + (a0DispW - origDispW) / 2 : 0;
  const origY = hasOriginal ? a0Y + (a0DispH - origDispH) / 2 : 0;

  const sizeLabel = (w: number, h: number) => {
    const match = KNOWN_SIZES.find(s =>
      (s.w === w && s.h === h) || (s.w === h && s.h === w)
    );
    return match ? match.label : null;
  };

  return (
    <div className="flex flex-col items-center" data-testid="size-comparator">
      <svg
        viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
        className="select-none w-full max-w-[280px] h-auto"
      >
        <defs>
          <pattern id="grid" width="10" height="10" patternUnits="userSpaceOnUse">
            <path d="M 10 0 L 0 0 0 10" fill="none" stroke="currentColor" strokeWidth="0.3" className="text-border" />
          </pattern>
        </defs>

        <rect x={a0X} y={a0Y} width={a0DispW} height={a0DispH} fill="url(#grid)" rx="3" />

        <rect
          x={a0X} y={a0Y} width={a0DispW} height={a0DispH}
          fill="none" stroke="currentColor" strokeWidth="1.5" rx="3"
          className="text-muted-foreground/40"
          strokeDasharray="6 3"
        />
        <text
          x={a0X + a0DispW / 2}
          y={a0Y + 14}
          textAnchor="middle"
          className="fill-muted-foreground/50"
          fontSize="10"
          fontWeight="600"
          fontFamily="system-ui"
        >
          A0 — {A0_W} × {A0_H}mm
        </text>

        {hasOriginal && !hasTarget && (
          <>
            <rect
              x={origX} y={origY} width={origDispW} height={origDispH}
              fill="currentColor" fillOpacity="0.06" stroke="currentColor" strokeWidth="1.5" rx="2"
              className="text-amber-500"
            />
            <text
              x={origX + origDispW / 2}
              y={origY + origDispH / 2 - 6}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-amber-600 dark:fill-amber-400"
              fontSize="11"
              fontWeight="700"
              fontFamily="system-ui"
            >
              Your Artwork
            </text>
            <text
              x={origX + origDispW / 2}
              y={origY + origDispH / 2 + 8}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-amber-600 dark:fill-amber-400"
              fontSize="10"
              fontWeight="500"
              fontFamily="ui-monospace, monospace"
            >
              {originalW} × {originalH}mm
            </text>
          </>
        )}

        {hasOriginal && hasTarget && (
          <>
            <rect
              x={origX} y={origY} width={origDispW} height={origDispH}
              fill="currentColor" fillOpacity="0.04" stroke="currentColor" strokeWidth="1" rx="2"
              className="text-amber-500"
              strokeDasharray="4 2"
            />
            {origDispW > 40 && origDispH > 18 && (
              <text
                x={origX + 4}
                y={origY + 12}
                className="fill-amber-500/70"
                fontSize="8"
                fontWeight="600"
                fontFamily="system-ui"
              >
                Original {originalW}×{originalH}
              </text>
            )}
          </>
        )}

        {hasTarget && (
          <>
            <rect
              x={targetX} y={targetY} width={targetDispW} height={targetDispH}
              fill="currentColor" fillOpacity="0.08" stroke="currentColor" strokeWidth="2" rx="2"
              className="text-primary"
            />
            <text
              x={targetX + targetDispW / 2}
              y={targetY + targetDispH / 2 - (targetDispH > 30 ? 6 : 0)}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-primary"
              fontSize={targetDispW < 50 ? "8" : "11"}
              fontWeight="700"
              fontFamily="system-ui"
            >
              {sizeLabel(targetW!, targetH!) || "Target"}
            </text>
            {targetDispH > 30 && (
              <text
                x={targetX + targetDispW / 2}
                y={targetY + targetDispH / 2 + 8}
                textAnchor="middle"
                dominantBaseline="middle"
                className="fill-primary"
                fontSize={targetDispW < 50 ? "7" : "10"}
                fontWeight="500"
                fontFamily="ui-monospace, monospace"
              >
                {targetW} × {targetH}mm
              </text>
            )}
          </>
        )}

        {!hasTarget && !hasOriginal && (
          <text
            x={CANVAS_W / 2}
            y={CANVAS_H / 2}
            textAnchor="middle"
            dominantBaseline="middle"
            className="fill-muted-foreground/40"
            fontSize="12"
            fontWeight="500"
            fontFamily="system-ui"
          >
            Select a size to preview
          </text>
        )}
      </svg>

      <div className="flex items-center justify-center gap-4 mt-1 text-[10px] font-semibold">
        {hasOriginal && (
          <span className="flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
            <span className="w-3 h-2 border border-amber-500 rounded-sm inline-block" style={{ borderStyle: hasTarget ? "dashed" : "solid" }} />
            Original
          </span>
        )}
        {hasTarget && (
          <span className="flex items-center gap-1.5 text-primary">
            <span className="w-3 h-2 border-2 border-primary rounded-sm inline-block" />
            Target
          </span>
        )}
        <span className="flex items-center gap-1.5 text-muted-foreground/50">
          <span className="w-3 h-2 border border-muted-foreground/40 rounded-sm inline-block" style={{ borderStyle: "dashed" }} />
          A0
        </span>
      </div>
    </div>
  );
}

function cropImageClientSide(file: globalThis.File, crop: CropCoordinates): Promise<Blob> {
  return new Promise((resolve, reject) => {
    if (!crop.cropWidth || !crop.cropHeight || crop.cropWidth <= 0 || crop.cropHeight <= 0) {
      reject(new Error('Invalid crop dimensions: width and height must be greater than zero'));
      return;
    }

    const TIMEOUT_MS = 15000;
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error('Client-side crop timed out after 15 seconds'));
      }
    }, TIMEOUT_MS);

    const finish = (fn: () => void) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        fn();
      }
    };

    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      try {
        const srcW = img.naturalWidth;
        const srcH = img.naturalHeight;
        const cx = Math.round(crop.cropX * srcW);
        const cy = Math.round(crop.cropY * srcH);
        const cw = Math.round(crop.cropWidth * srcW);
        const ch = Math.round(crop.cropHeight * srcH);

        if (cw <= 0 || ch <= 0) {
          URL.revokeObjectURL(url);
          finish(() => reject(new Error(`Crop region too small: ${cw}×${ch}px from ${srcW}×${srcH}px source`)));
          return;
        }

        const canvas = document.createElement('canvas');
        canvas.width = cw;
        canvas.height = ch;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          URL.revokeObjectURL(url);
          finish(() => reject(new Error('Canvas context unavailable')));
          return;
        }
        ctx.drawImage(img, cx, cy, cw, ch, 0, 0, cw, ch);
        URL.revokeObjectURL(url);

        const ext = file.name.split('.').pop()?.toLowerCase() || 'png';
        const mime = ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : 'image/png';
        const quality = mime === 'image/jpeg' ? 0.95 : undefined;
        canvas.toBlob((blob) => {
          if (!blob) {
            finish(() => reject(new Error('Canvas toBlob produced no output')));
            return;
          }
          console.log(`[CROP] Client-side crop success: ${cw}×${ch}px → ${blob.size} bytes`);
          finish(() => resolve(blob));
        }, mime, quality);
      } catch (e) {
        URL.revokeObjectURL(url);
        console.error('[CROP] Client-side crop error:', e);
        finish(() => reject(e instanceof Error ? e : new Error(String(e))));
      }
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      console.error('[CROP] Failed to load image for client-side crop');
      finish(() => reject(new Error('Failed to load image for crop')));
    };
    img.src = url;
  });
}

function optimizeImageForPrint(
  file: globalThis.File | Blob,
  targetWidthMm: number,
  targetHeightMm: number,
): Promise<Blob> {
  const DPI = 300;
  const MM_PER_INCH = 25.4;
  const targetPxW = Math.round((targetWidthMm / MM_PER_INCH) * DPI);
  const targetPxH = Math.round((targetHeightMm / MM_PER_INCH) * DPI);

  return new Promise((resolve, reject) => {
    const TIMEOUT_MS = 30000;
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) { settled = true; reject(new Error('Print optimization timed out')); }
    }, TIMEOUT_MS);
    const finish = (fn: () => void) => { if (!settled) { settled = true; clearTimeout(timer); fn(); } };

    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      try {
        const srcW = img.naturalWidth;
        const srcH = img.naturalHeight;

        const srcAspect = srcW / srcH;
        const tgtAspect = targetPxW / targetPxH;
        let fitW: number, fitH: number;
        if (srcAspect > tgtAspect) {
          fitW = targetPxW;
          fitH = Math.round(targetPxW / srcAspect);
        } else {
          fitH = targetPxH;
          fitW = Math.round(targetPxH * srcAspect);
        }

        if (fitW >= srcW && fitH >= srcH) {
          fitW = srcW;
          fitH = srcH;
          console.log(`[OPTIMIZE] Image at/below 300DPI target (${srcW}×${srcH}), flattening only`);
        }

        const canvas = document.createElement('canvas');
        canvas.width = fitW;
        canvas.height = fitH;
        const ctx = canvas.getContext('2d', { alpha: false, willReadFrequently: false });
        if (!ctx) {
          URL.revokeObjectURL(url);
          finish(() => reject(new Error('Canvas 2D context unavailable')));
          return;
        }

        ctx.fillStyle = '#FFFFFF';
        ctx.fillRect(0, 0, fitW, fitH);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(img, 0, 0, fitW, fitH);
        URL.revokeObjectURL(url);

        canvas.toBlob(
          (blob) => {
            canvas.width = 1;
            canvas.height = 1;
            if (!blob) {
              finish(() => reject(new Error('Canvas toBlob produced no output')));
              return;
            }
            const reduction = file.size > 0 ? ((1 - blob.size / file.size) * 100).toFixed(0) : '0';
            console.log(
              `[OPTIMIZE] Print-ready: ${srcW}×${srcH} → ${fitW}×${fitH}px @ 300DPI | ` +
              `${(file.size / 1024).toFixed(0)}KB → ${(blob.size / 1024).toFixed(0)}KB (${reduction}% smaller)`
            );
            finish(() => resolve(blob));
          },
          'image/jpeg',
          0.95,
        );
      } catch (e) {
        URL.revokeObjectURL(url);
        finish(() => reject(e instanceof Error ? e : new Error(String(e))));
      }
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      finish(() => reject(new Error('Failed to load image for print optimization')));
    };
    img.src = url;
  });
}

interface BatchJob {
  file: globalThis.File;
  jobId?: number;
  status: 'pending' | 'uploading' | 'processing' | 'complete' | 'failed';
}

export function FileUpload() {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const uploadJob = useUploadJob();
  const betaMode = useBeta();
  const [isHovering, setIsHovering] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const [bleedOptions, setBleedOptions] = useState<BleedOptions>({ ...defaultBleedOptions });
  const [widthInput, setWidthInput] = useState("");
  const [batchJobs, setBatchJobs] = useState<BatchJob[]>([]);
  const [batchProcessing, setBatchProcessing] = useState(false);
  const batchPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (batchPollRef.current) clearInterval(batchPollRef.current);
    };
  }, []);
  const [heightInput, setHeightInput] = useState("");
  const [orientation, setOrientation] = useState<"portrait" | "landscape">("portrait");
  const [originalDims, setOriginalDims] = useState<{ w: number; h: number; pxW?: number; pxH?: number } | null>(null);
  const [previewFile, setPreviewFile] = useState<globalThis.File | null>(null);
  const [showCropTool, setShowCropTool] = useState(false);
  const [pendingCropCoords, setPendingCropCoords] = useState<CropCoordinates | null>(null);
  const [wizardStage, setWizardStage] = useState<1 | 2 | 3>(1);
  const [isCropping, setIsCropping] = useState(false);
  const [isOptimizing, setIsOptimizing] = useState(false);
  const [preserveBleed, setPreserveBleed] = useState(false);
  const stage3Ref = useRef<HTMLDivElement>(null);

  const updateOption = <K extends keyof BleedOptions>(key: K, value: BleedOptions[K]) => {
    setBleedOptions(prev => ({ ...prev, [key]: value }));
  };

  const updateDimensions = (w: string, h: string) => {
    setWidthInput(w);
    setHeightInput(h);
    const wNum = parseFloat(w);
    const hNum = parseFloat(h);
    setBleedOptions(prev => ({
      ...prev,
      targetWidth: (wNum > 0 && !isNaN(wNum)) ? wNum : null,
      targetHeight: (hNum > 0 && !isNaN(hNum)) ? hNum : null,
    }));
  };

  const swapOrientation = (newOrientation: "portrait" | "landscape") => {
    if (newOrientation === orientation) return;
    setOrientation(newOrientation);
    if (widthInput && heightInput) {
      updateDimensions(heightInput, widthInput);
    }
  };

  const [stagedFile, setStagedFile] = useState<globalThis.File | null>(null);
  const [stagedPreviewUrl, setStagedPreviewUrl] = useState<string | null>(null);

  const xrayAnalysis = useMemo(() => {
    if (!originalDims || !originalDims.pxW || !originalDims.pxH) return null;
    const targetW = parseFloat(widthInput);
    const targetH = parseFloat(heightInput);
    if (!targetW || !targetH || targetW <= 0 || targetH <= 0) return null;

    const pxW = originalDims.pxW;
    const pxH = originalDims.pxH;
    const artworkWMm = originalDims.w;
    const artworkHMm = originalDims.h;
    const effectiveDpi = Math.round(Math.min(pxW / (targetW / 25.4), pxH / (targetH / 25.4)));
    const bleedMm = bleedOptions.adjustableBleedSize;
    const totalNeededW = targetW + 2 * bleedMm;
    const totalNeededH = targetH + 2 * bleedMm;

    const ext = stagedFile?.name.split('.').pop()?.toLowerCase() || '';
    const colorSpace = ext === 'png' ? 'RGB (PNG)' : ext === 'jpg' || ext === 'jpeg' ? 'RGB (JPEG)' : 'Unknown';

    const excessW = artworkWMm - targetW;
    const excessH = artworkHMm - targetH;
    const minExcess = Math.min(excessW, excessH);
    const perSideBleed = minExcess / 2;

    let scenario: 'true-bleed' | 'partial-bleed' | 'no-bleed';
    if (perSideBleed >= bleedMm) {
      scenario = 'true-bleed';
    } else if (perSideBleed > 1) {
      scenario = 'partial-bleed';
    } else {
      scenario = 'no-bleed';
    }

    return {
      pxW, pxH,
      artworkWMm, artworkHMm,
      effectiveDpi,
      colorSpace,
      bleedMm,
      totalNeededW, totalNeededH,
      perSideBleed: Math.max(0, perSideBleed),
      scenario,
    };
  }, [originalDims, widthInput, heightInput, bleedOptions.adjustableBleedSize, stagedFile]);

  useEffect(() => {
    return () => {
      if (stagedPreviewUrl) URL.revokeObjectURL(stagedPreviewUrl);
    };
  }, [stagedPreviewUrl]);

  const handleFilePreview = useCallback(async (file: globalThis.File) => {
    setPreviewFile(file);
    const dims = await detectFileDimensions(file);
    if (dims) {
      setOriginalDims(dims);
    }
  }, []);

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    const oversized = acceptedFiles.filter(f => f.size > 50 * 1024 * 1024);
    if (oversized.length > 0) {
      toast({
        title: "File too large",
        description: `${oversized.length} file(s) exceed the 50MB limit.`,
        variant: "destructive",
      });
    }
    const validFiles = acceptedFiles.filter(f => f.size <= 50 * 1024 * 1024);
    if (validFiles.length === 0) return;

    if (validFiles.length === 1) {
      const file = validFiles[0];
      handleFilePreview(file);
      setStagedFile(file);
      setBatchJobs([]);
      const webNativeImageTypes = ["image/png", "image/jpeg", "image/gif", "image/svg+xml", "image/webp"];
      if (webNativeImageTypes.includes(file.type)) {
        setStagedPreviewUrl(URL.createObjectURL(file));
      } else {
        setStagedPreviewUrl(null);
        const formData = new FormData();
        formData.append("file", file);
        fetch("/api/preview-pdf-page", { method: "POST", body: formData })
          .then(r => r.ok ? r.json() : null)
          .then(data => {
            if (data?.previewUrl) {
              setStagedPreviewUrl(data.previewUrl);
            }
          })
          .catch(() => {});
      }
      setPendingCropCoords(null);
      setShowCropTool(false);
      setPreserveBleed(false);
      setWizardStage(2);
    } else {
      setStagedFile(null);
      setStagedPreviewUrl(null);
      setPreviewFile(null);
      setOriginalDims(null);
      setPendingCropCoords(null);
      setShowCropTool(false);
      setWizardStage(1);
      setBatchJobs(validFiles.map(f => ({ file: f, status: 'pending' as const })));
    }
  }, [toast, handleFilePreview]);

  const handleStartProcess = useCallback(async () => {
    if (batchJobs.length > 1) {
      setBatchProcessing(true);
      try {
        const formData = new FormData();
        batchJobs.forEach(bj => formData.append('files', bj.file));
        if (bleedOptions) {
          formData.append('bleedOptions', JSON.stringify(bleedOptions));
        }
        setBatchJobs(prev => prev.map(bj => ({ ...bj, status: 'uploading' as const })));
        const resp = await fetch('/api/batch-upload', { method: 'POST', body: formData });
        if (!resp.ok) throw new Error('Batch upload failed');
        const data = await resp.json();
        const jobIds: number[] = data.jobIds;
        setBatchJobs(prev => prev.map((bj, i) => ({ ...bj, jobId: jobIds[i], status: 'processing' as const })));
        toast({ title: "Batch uploaded", description: `${jobIds.length} files are being analyzed...` });

        if (batchPollRef.current) clearInterval(batchPollRef.current);
        batchPollRef.current = setInterval(async () => {
          let allDone = true;
          const updated: BatchJob[] = [];
          for (let i = 0; i < jobIds.length; i++) {
            try {
              const jr = await fetch(`/api/jobs/${jobIds[i]}`);
              const jd = await jr.json();
              const st = jd.status === 'complete' ? 'complete' as const : jd.status === 'failed' ? 'failed' as const : 'processing' as const;
              if (st === 'processing') allDone = false;
              updated.push({ file: batchJobs[i].file, jobId: jobIds[i], status: st });
            } catch {
              updated.push({ file: batchJobs[i].file, jobId: jobIds[i], status: 'processing' as const });
              allDone = false;
            }
          }
          setBatchJobs(updated);
          if (allDone) {
            if (batchPollRef.current) clearInterval(batchPollRef.current);
            batchPollRef.current = null;
            setBatchProcessing(false);
            toast({ title: "Batch complete", description: "All files have been processed." });
          }
        }, 3000);
      } catch (error) {
        setBatchProcessing(false);
        toast({ title: "Batch upload failed", description: error instanceof Error ? error.message : "Unknown error", variant: "destructive" });
      }
      return;
    }

    if (!stagedFile) {
      console.error('[UPLOAD] handleStartProcess called but stagedFile is null');
      toast({ title: "No file selected", description: "Please upload a file first.", variant: "destructive" });
      return;
    }
    try {
      const uploadBleedOptions = { ...bleedOptions };
      let fileToUpload: globalThis.File | Blob = stagedFile;
      let uploadFileName: string = stagedFile.name;
      const ext = stagedFile.name.split('.').pop()?.toLowerCase() || '';
      const isImage = ['png', 'jpg', 'jpeg'].includes(ext);

      if (pendingCropCoords && isImage && !isFullPageCropBox(pendingCropCoords)) {
        console.log(`[UPLOAD] Client-side crop: cropping ${stagedFile.name} in browser before upload`);
        setIsCropping(true);
        try {
          const croppedBlob = await cropImageClientSide(stagedFile, pendingCropCoords);
          fileToUpload = croppedBlob;
          uploadFileName = stagedFile.name.replace(/(\.[^.]+)$/, '_cropped$1');
          console.log(`[UPLOAD] Client-side crop complete: ${croppedBlob.size} bytes (original ${stagedFile.size} bytes)`);
        } catch (cropErr) {
          console.error('[UPLOAD] Client-side crop failed:', cropErr);
          toast({
            title: "Crop failed",
            description: cropErr instanceof Error ? cropErr.message : "Could not apply crop to image",
            variant: "destructive",
          });
          return;
        } finally {
          setIsCropping(false);
        }
      } else if (pendingCropCoords) {
        (uploadBleedOptions as any).cropX = pendingCropCoords.cropX;
        (uploadBleedOptions as any).cropY = pendingCropCoords.cropY;
        (uploadBleedOptions as any).cropWidth = pendingCropCoords.cropWidth;
        (uploadBleedOptions as any).cropHeight = pendingCropCoords.cropHeight;
        if (isFullPageCropBox(pendingCropCoords)) {
          (uploadBleedOptions as any).is_no_crop = true;
          console.log(`[UPLOAD] NO_CROP_FULL_PAGE: crop_box = full page (0,0,1,1) — raster-first handoff`);
        } else {
          console.log(`[UPLOAD] PDF crop coords attached: x=${pendingCropCoords.cropX.toFixed(4)}, y=${pendingCropCoords.cropY.toFixed(4)}, w=${pendingCropCoords.cropWidth.toFixed(4)}, h=${pendingCropCoords.cropHeight.toFixed(4)}`);
        }
      } else {
        // No Crop Needed / submit-as-is: always populate a full-page crop_box so
        // PyMuPDF never sees a missing box (Document Closed / hung job page).
        (uploadBleedOptions as any).cropX = FULL_PAGE_CROP_BOX.cropX;
        (uploadBleedOptions as any).cropY = FULL_PAGE_CROP_BOX.cropY;
        (uploadBleedOptions as any).cropWidth = FULL_PAGE_CROP_BOX.cropWidth;
        (uploadBleedOptions as any).cropHeight = FULL_PAGE_CROP_BOX.cropHeight;
        (uploadBleedOptions as any).is_no_crop = true;
        console.log(`[UPLOAD] NO_CROP_FULL_PAGE: crop_box = full page (0,0,1,1) — raster-first handoff`);
      }

      if (preserveBleed) {
        (uploadBleedOptions as any).preserveBleed = true;
        console.log(`[UPLOAD] preserveBleed=true — bypassing scale_fill, preserving original bleed`);
      }

      if (isImage && uploadBleedOptions.targetWidth && uploadBleedOptions.targetHeight && !preserveBleed) {
        setIsOptimizing(true);
        try {
          const prevSize = fileToUpload instanceof Blob ? fileToUpload.size : 0;
          let optimized: Blob;
          if (betaMode) {
            console.log('[BETA-OPTIMIZE] Routing through Web Worker + blob cache');
            optimized = await optimizeImageViaWorker(
              fileToUpload,
              uploadBleedOptions.targetWidth,
              uploadBleedOptions.targetHeight,
            );
          } else {
            optimized = await optimizeImageForPrint(
              fileToUpload,
              uploadBleedOptions.targetWidth,
              uploadBleedOptions.targetHeight,
            );
          }
          fileToUpload = optimized;
          uploadFileName = uploadFileName.replace(/(\.[^.]+)$/, '.jpg');
          (uploadBleedOptions as any).clientOptimized = true;
          console.log(`[OPTIMIZE] Using print-normalized blob: ${(optimized.size / 1024).toFixed(0)}KB (was ${(prevSize / 1024).toFixed(0)}KB)`);
        } catch (optErr) {
          console.warn('[OPTIMIZE] Client-side optimization failed (non-fatal), sending original:', optErr);
        } finally {
          setIsOptimizing(false);
        }
      } else if (isImage && preserveBleed) {
        setIsOptimizing(true);
        try {
          const prevSize = fileToUpload instanceof Blob ? fileToUpload.size : 0;
          const img = new Image();
          const url = URL.createObjectURL(fileToUpload instanceof File ? fileToUpload : new Blob([fileToUpload]));
          await new Promise<void>((resolve, reject) => {
            img.onload = () => resolve();
            img.onerror = () => reject(new Error('Failed to load image for pre-compression'));
            img.src = url;
          });
          URL.revokeObjectURL(url);
          const MAX_PRESERVE_PX = 4000;
          let drawW = img.naturalWidth;
          let drawH = img.naturalHeight;
          if (Math.max(drawW, drawH) > MAX_PRESERVE_PX) {
            const shrink = MAX_PRESERVE_PX / Math.max(drawW, drawH);
            drawW = Math.round(drawW * shrink);
            drawH = Math.round(drawH * shrink);
          }
          const canvas = document.createElement('canvas');
          canvas.width = drawW;
          canvas.height = drawH;
          const ctx = canvas.getContext('2d')!;
          ctx.fillStyle = '#ffffff';
          ctx.fillRect(0, 0, drawW, drawH);
          ctx.drawImage(img, 0, 0, drawW, drawH);
          const blob = await new Promise<Blob>((resolve, reject) => {
            canvas.toBlob(b => b ? resolve(b) : reject(new Error('Canvas toBlob failed')), 'image/jpeg', 0.95);
          });
          fileToUpload = blob;
          uploadFileName = uploadFileName.replace(/(\.[^.]+)$/, '.jpg');
          (uploadBleedOptions as any).clientPreCompressed = true;
          console.log(`[PRESERVE-COMPRESS] Pre-compressed for preserveBleed: ${(blob.size / 1024).toFixed(0)}KB (was ${(prevSize / 1024).toFixed(0)}KB), ${drawW}x${drawH}px`);
        } catch (compErr) {
          console.warn('[PRESERVE-COMPRESS] Pre-compression failed (non-fatal), sending original:', compErr);
        } finally {
          setIsOptimizing(false);
        }
      }

      console.log(`[UPLOAD] Target size: ${uploadBleedOptions.targetWidth}×${uploadBleedOptions.targetHeight}mm`);
      const response = await uploadJob.mutateAsync({ file: fileToUpload, fileName: uploadFileName, bleedOptions: uploadBleedOptions });
      toast({
        title: "Upload successful",
        description: "Your file is being analyzed...",
      });
      setLocation(`/job/${response.jobId}`);
    } catch (error) {
      console.error('[UPLOAD] Upload failed:', error);
      toast({
        title: "Upload failed",
        description: error instanceof Error ? error.message : "An unknown error occurred",
        variant: "destructive",
      });
    }
  }, [stagedFile, uploadJob, bleedOptions, setLocation, toast, batchJobs, pendingCropCoords, preserveBleed]);


  const handleClearStaged = useCallback(() => {
    if (stagedPreviewUrl) URL.revokeObjectURL(stagedPreviewUrl);
    setStagedFile(null);
    setStagedPreviewUrl(null);
    setPreviewFile(null);
    setOriginalDims(null);
    setBatchJobs([]);
    setBatchProcessing(false);
    setPendingCropCoords(null);
    setShowCropTool(false);
    setWizardStage(1);
    setIsCropping(false);
    setIsOptimizing(false);
    setPreserveBleed(false);
    if (batchPollRef.current) { clearInterval(batchPollRef.current); batchPollRef.current = null; }
  }, [stagedPreviewUrl]);

  const { getRootProps, getInputProps, isDragActive, isDragReject } = useDropzone({
    onDrop,
    maxFiles: 20,
    accept: {
      'application/pdf': ['.pdf'],
      'image/jpeg': ['.jpg', '.jpeg'],
      'image/png': ['.png'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx']
    }
  });

  const hasSizeSet = !!(parseFloat(widthInput) > 0 && parseFloat(heightInput) > 0);

  const sizeComparatorProps = {
    targetW: parseFloat(widthInput) || null,
    targetH: parseFloat(heightInput) || null,
    originalW: originalDims?.w || null,
    originalH: originalDims?.h || null,
  };

  const scrollToStage3 = () => {
    setTimeout(() => {
      stage3Ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 150);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 mb-1" data-testid="wizard-progress">
        {[
          { num: 1, label: "Upload" },
          { num: 2, label: "Size" },
          { num: 3, label: "Crop & Submit" },
        ].map((s, i) => (
          <div key={s.num} className="flex items-center gap-1.5">
            <div className={cn(
              "w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold transition-colors",
              wizardStage === s.num ? "bg-primary text-white" :
              wizardStage > s.num ? "bg-green-500 text-white" :
              "bg-muted text-muted-foreground"
            )}>
              {wizardStage > s.num ? <CheckCircle2 className="w-3.5 h-3.5" /> : s.num}
            </div>
            <span className={cn(
              "text-xs font-medium hidden sm:inline",
              wizardStage === s.num ? "text-foreground" : "text-muted-foreground"
            )}>{s.label}</span>
            {i < 2 && <div className="w-6 h-px bg-border" />}
          </div>
        ))}
      </div>

      {wizardStage >= 2 && stagedFile && (
        <div data-testid="panel-size-selection">
          <Card className="p-4 glass-card tech-corners mb-3" data-testid="panel-staged-preview">
            <div className="flex items-center gap-3">
              {stagedPreviewUrl ? (
                <img
                  src={stagedPreviewUrl}
                  alt="Artwork preview"
                  className="w-16 h-16 object-contain rounded-lg border border-border/40 bg-muted/30 shrink-0"
                  data-testid="img-staged-preview"
                />
              ) : (
                <div className="w-16 h-16 rounded-lg border border-border/40 bg-muted/30 flex items-center justify-center shrink-0">
                  <File className="w-8 h-8 text-primary/60" />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <span className="text-sm font-semibold text-foreground truncate block" data-testid="text-staged-filename">{stagedFile.name}</span>
                <span className="text-xs text-muted-foreground">{(stagedFile.size / 1024 / 1024).toFixed(1)} MB</span>
                {originalDims && (
                  <span className="text-xs text-muted-foreground ml-2">• {originalDims.w} × {originalDims.h}mm @ 300 DPI</span>
                )}
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={handleClearStaged}
                className="shrink-0 text-muted-foreground hover:text-destructive"
                data-testid="button-remove-file"
                title="Remove file"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
          </Card>

          {wizardStage === 2 && (
            <Card className="p-4 glass-card tech-corners" data-testid="panel-target-size">
              <div className="flex items-center gap-3 mb-3">
                <Ruler className="w-4 h-4 text-primary" />
                <span className="text-sm font-semibold text-foreground">Step 2: Choose Target Size (mm)</span>
                <span className="text-xs text-amber-600 dark:text-amber-400 font-medium">— required</span>
              </div>

              <div className="space-y-4">
                <div className="grid grid-cols-[1fr_auto] gap-4 items-start">
                  <div className="space-y-3">
                    {originalDims && (
                      <div className="flex items-center gap-2 bg-amber-500/10 border border-amber-500/25 rounded-lg p-2.5" data-testid="original-size-badge">
                        <ImageIcon className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
                        <div>
                          <span className="text-xs font-semibold text-amber-700 dark:text-amber-400">Your artwork:</span>
                          <span className="text-xs font-mono font-bold text-amber-700 dark:text-amber-400 ml-1.5">{originalDims.w} × {originalDims.h}mm</span>
                          <span className="text-[10px] text-amber-600/70 dark:text-amber-400/60 ml-1">(at 300 DPI)</span>
                        </div>
                      </div>
                    )}

                    <div>
                      <Label className="text-xs text-muted-foreground mb-1 block">Preset Sizes</Label>
                      <Select
                        value={
                          (widthInput === "90" && heightInput === "50") || (widthInput === "50" && heightInput === "90") ? "business-card" :
                          (widthInput === "105" && heightInput === "148") || (widthInput === "148" && heightInput === "105") ? "a6" :
                          (widthInput === "148" && heightInput === "210") || (widthInput === "210" && heightInput === "148") ? "a5" :
                          (widthInput === "210" && heightInput === "297") || (widthInput === "297" && heightInput === "210") ? "a4" :
                          (widthInput === "297" && heightInput === "420") || (widthInput === "420" && heightInput === "297") ? "a3" :
                          (widthInput === "420" && heightInput === "594") || (widthInput === "594" && heightInput === "420") ? "a2" :
                          (widthInput === "594" && heightInput === "841") || (widthInput === "841" && heightInput === "594") ? "a1" :
                          (widthInput === "841" && heightInput === "1189") || (widthInput === "1189" && heightInput === "841") ? "a0" :
                          "custom"
                        }
                        onValueChange={(v) => {
                          const sizes: Record<string, [number, number]> = {
                            "business-card": [90, 50],
                            "a6": [105, 148],
                            "a5": [148, 210],
                            "a4": [210, 297],
                            "a3": [297, 420],
                            "a2": [420, 594],
                            "a1": [594, 841],
                            "a0": [841, 1189],
                          };
                          if (v === "custom") {
                            if (originalDims) {
                              updateDimensions(String(originalDims.w), String(originalDims.h));
                            } else {
                              updateDimensions("", "");
                            }
                            return;
                          }
                          const [short, long] = sizes[v] || [0, 0];
                          if (orientation === "portrait") {
                            updateDimensions(String(short), String(long));
                          } else {
                            updateDimensions(String(long), String(short));
                          }
                        }}
                      >
                        <SelectTrigger data-testid="select-preset-size">
                          <SelectValue placeholder="Choose a preset size..." />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="custom">Custom Size</SelectItem>
                          <SelectItem value="business-card">Business Card — 90 × 50 mm</SelectItem>
                          <SelectItem value="a6">A6 — 105 × 148 mm</SelectItem>
                          <SelectItem value="a5">A5 — 148 × 210 mm</SelectItem>
                          <SelectItem value="a4">A4 — 210 × 297 mm</SelectItem>
                          <SelectItem value="a3">A3 — 297 × 420 mm</SelectItem>
                          <SelectItem value="a2">A2 — 420 × 594 mm</SelectItem>
                          <SelectItem value="a1">A1 — 594 × 841 mm</SelectItem>
                          <SelectItem value="a0">A0 — 841 × 1189 mm</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    <div>
                      <Label className="text-xs text-muted-foreground mb-1 block">Orientation</Label>
                      <div className="flex border border-border/60 rounded-md overflow-hidden w-fit">
                        <button
                          type="button"
                          onClick={() => swapOrientation("portrait")}
                          className={cn(
                            "flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors",
                            orientation === "portrait"
                              ? "bg-primary text-primary-foreground"
                              : "bg-background text-muted-foreground hover:bg-muted/50"
                          )}
                          data-testid="button-portrait"
                        >
                          <RectangleVertical className="w-4 h-4" />
                          Portrait
                        </button>
                        <button
                          type="button"
                          onClick={() => swapOrientation("landscape")}
                          className={cn(
                            "flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors border-l border-border/60",
                            orientation === "landscape"
                              ? "bg-primary text-primary-foreground"
                              : "bg-background text-muted-foreground hover:bg-muted/50"
                          )}
                          data-testid="button-landscape"
                        >
                          <RectangleHorizontal className="w-4 h-4" />
                          Landscape
                        </button>
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      <div className="flex-1">
                        <Label className="text-xs text-muted-foreground mb-1 block">Width (mm)</Label>
                        <input
                          type="number"
                          min="1"
                          max="3000"
                          step="0.1"
                          placeholder="e.g. 148"
                          value={widthInput}
                          onChange={(e) => updateDimensions(e.target.value, heightInput)}
                          className="w-full px-3 py-2 text-sm border border-border/60 rounded-md bg-background text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/30"
                          data-testid="input-target-width"
                        />
                      </div>
                      <span className="text-muted-foreground font-bold mt-5">×</span>
                      <div className="flex-1">
                        <Label className="text-xs text-muted-foreground mb-1 block">Height (mm)</Label>
                        <input
                          type="number"
                          min="1"
                          max="3000"
                          step="0.1"
                          placeholder="e.g. 210"
                          value={heightInput}
                          onChange={(e) => updateDimensions(widthInput, e.target.value)}
                          className="w-full px-3 py-2 text-sm border border-border/60 rounded-md bg-background text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/30"
                          data-testid="input-target-height"
                        />
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center justify-center shrink-0">
                    <SizeComparator {...sizeComparatorProps} />
                  </div>
                </div>

                <Button
                  onClick={() => {
                    setWizardStage(3);
                    scrollToStage3();
                  }}
                  disabled={!hasSizeSet}
                  className="w-full gap-2 font-semibold"
                  data-testid="button-proceed-to-crop"
                >
                  <CheckCircle2 className="w-4 h-4" />
                  Next: Crop & Submit
                </Button>
              </div>
            </Card>
          )}
        </div>
      )}

      {wizardStage >= 3 && stagedFile && (
        <div ref={stage3Ref}>

        {pendingCropCoords && !isFullPageCropBox(pendingCropCoords) && (
          <Card className="p-3 mb-3 border-green-500/30 bg-green-500/5 glass-card tech-corners" data-testid="panel-crop-handoff-summary">
            <div className="flex items-center gap-2">
              <Scissors className="w-4 h-4 text-green-600 dark:text-green-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <span className="text-xs font-bold text-green-700 dark:text-green-400">Crop locked — aspect ratio {widthInput}:{heightInput}</span>
                <span className="text-[10px] text-green-600/80 dark:text-green-400/70 ml-2 font-mono">
                  region: ({Math.round(pendingCropCoords.cropX * 100)}%, {Math.round(pendingCropCoords.cropY * 100)}%) {Math.round(pendingCropCoords.cropWidth * 100)}% × {Math.round(pendingCropCoords.cropHeight * 100)}%
                </span>
              </div>
              <button
                onClick={() => { setPendingCropCoords(null); }}
                className="text-xs text-amber-600 dark:text-amber-400 hover:underline font-medium shrink-0"
                data-testid="button-recrop"
              >
                Re-crop
              </button>
            </div>
          </Card>
        )}

        {!showCropTool && (
          <Card className="p-5 glass-card tech-corners mb-3" data-testid="stage3-crop-decision">
            <div className="flex items-center gap-2 mb-1">
              <Crop className="w-4 h-4 text-amber-600 dark:text-amber-400" />
              <span className="text-sm font-semibold text-foreground">Step 3: Crop & Submit</span>
            </div>
            <p className="text-[11px] text-muted-foreground mb-3">Crop is locked to your target size aspect ratio ({widthInput} × {heightInput}mm). Use this to remove false bleeds or mockup borders.</p>

            {xrayAnalysis && (
              <div className="mb-3 space-y-2" data-testid="xray-dashboard">
                <div className="grid grid-cols-3 gap-2 bg-muted/30 border border-border/40 rounded-lg p-3" data-testid="xray-stats">
                  <div className="text-center">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 mb-0.5">Color Space</div>
                    <div className="text-xs font-bold text-foreground" data-testid="xray-colorspace">{xrayAnalysis.colorSpace}</div>
                  </div>
                  <div className="text-center border-x border-border/30">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 mb-0.5">Effective DPI</div>
                    <div className={cn("text-xs font-bold", xrayAnalysis.effectiveDpi >= 300 ? "text-green-600 dark:text-green-400" : xrayAnalysis.effectiveDpi >= 150 ? "text-amber-600 dark:text-amber-400" : "text-red-600 dark:text-red-400")} data-testid="xray-dpi">{xrayAnalysis.effectiveDpi}</div>
                  </div>
                  <div className="text-center">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 mb-0.5">Physical Size</div>
                    <div className="text-xs font-bold text-foreground" data-testid="xray-physical-size">{xrayAnalysis.artworkWMm} × {xrayAnalysis.artworkHMm}mm</div>
                  </div>
                </div>
                <div className="text-[10px] text-muted-foreground/70 text-center font-mono" data-testid="xray-pixel-dims">{xrayAnalysis.pxW} × {xrayAnalysis.pxH}px @ 300 DPI</div>

                {xrayAnalysis.scenario === 'true-bleed' && (
                  <div className="flex items-start gap-2 bg-green-500/10 border border-green-500/30 rounded-lg px-3 py-2.5" data-testid="xray-alert-true-bleed">
                    <Shield className="w-4 h-4 text-green-600 dark:text-green-400 shrink-0 mt-0.5" />
                    <div>
                      <span className="text-xs font-bold text-green-700 dark:text-green-400">True Bleed Detected</span>
                      <span className="text-[11px] text-green-600/80 dark:text-green-400/70 block">Your artwork has ~{xrayAnalysis.perSideBleed.toFixed(1)}mm bleed per side (need {xrayAnalysis.bleedMm}mm). We recommend clicking <strong>No Crop Needed</strong> to preserve it.</span>
                    </div>
                  </div>
                )}

                {xrayAnalysis.scenario === 'partial-bleed' && (
                  <div className="flex items-start gap-2 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2.5" data-testid="xray-alert-partial-bleed">
                    <Eye className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                    <div>
                      <span className="text-xs font-bold text-amber-700 dark:text-amber-400">Partial Bleed Detected</span>
                      <span className="text-[11px] text-amber-600/80 dark:text-amber-400/70 block">Your artwork has ~{xrayAnalysis.perSideBleed.toFixed(1)}mm bleed per side (need {xrayAnalysis.bleedMm}mm). We recommend clicking <strong>No Crop Needed</strong> to let AI extend the rest.</span>
                    </div>
                  </div>
                )}

                {xrayAnalysis.scenario === 'no-bleed' && (
                  <div className="flex items-start gap-2 bg-blue-500/10 border border-blue-500/30 rounded-lg px-3 py-2.5" data-testid="xray-alert-no-bleed">
                    <Target className="w-4 h-4 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
                    <div>
                      <span className="text-xs font-bold text-blue-700 dark:text-blue-400">No Bleed Detected</span>
                      <span className="text-[11px] text-blue-600/80 dark:text-blue-400/70 block">Your artwork matches or is smaller than the target size. Please use the crop box to frame your artwork, or submit as-is.</span>
                    </div>
                  </div>
                )}
              </div>
            )}


            {preserveBleed && (
              <div className="mb-3 flex items-center gap-2 bg-green-500/10 border border-green-500/25 rounded-lg px-3 py-2" data-testid="indicator-preserve-bleed">
                <Shield className="w-3.5 h-3.5 text-green-600 dark:text-green-400 shrink-0" />
                <span className="text-xs font-medium text-green-700 dark:text-green-400">Bleed preserved — original dimensions will pass through without rescaling</span>
                <button
                  onClick={() => setPreserveBleed(false)}
                  className="text-xs text-amber-600 dark:text-amber-400 hover:underline font-medium shrink-0 ml-auto"
                  data-testid="button-undo-preserve-bleed"
                >
                  Undo
                </button>
              </div>
            )}

            <div className="flex gap-3 mb-3">
              <Button
                onClick={() => { setPreserveBleed(false); setShowCropTool(true); }}
                variant="outline"
                className="flex-1 gap-2"
                data-testid="button-yes-crop"
              >
                <Crop className="w-4 h-4" />
                Crop artwork
              </Button>
              <Button
                onClick={() => {
                  setPendingCropCoords(FULL_PAGE_CROP_BOX);
                  setPreserveBleed(true);
                }}
                variant={xrayAnalysis && (xrayAnalysis.scenario === 'true-bleed' || xrayAnalysis.scenario === 'partial-bleed') ? "default" : "ghost"}
                className={cn("flex-1 gap-2", xrayAnalysis && (xrayAnalysis.scenario === 'true-bleed' || xrayAnalysis.scenario === 'partial-bleed') && "bg-green-600 hover:bg-green-700 text-white")}
                data-testid="button-skip-crop"
              >
                <Shield className="w-4 h-4" />
                No crop needed
              </Button>
            </div>

            <div className="border-t border-border/30 pt-3">
              <div className="flex items-center gap-2 mb-2">
                <File className="w-4 h-4 text-primary shrink-0" />
                <span className="text-sm font-semibold text-foreground truncate" data-testid="text-staged-filename-stage3">{stagedFile.name}</span>
                <span className="text-xs text-muted-foreground ml-auto shrink-0">{(stagedFile.size / 1024 / 1024).toFixed(1)} MB</span>
              </div>
              {pendingCropCoords && !isFullPageCropBox(pendingCropCoords) && (
                <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/25 rounded-lg px-3 py-2 mb-2" data-testid="indicator-crop-active-stage3">
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-600 shrink-0" />
                  <span className="text-xs font-medium text-green-700 dark:text-green-400">Crop active: {Math.round(pendingCropCoords.cropWidth * 100)}% × {Math.round(pendingCropCoords.cropHeight * 100)}% of original</span>
                </div>
              )}
              <div className="flex gap-2">
                <Button
                  onClick={handleStartProcess}
                  disabled={uploadJob.isPending || isCropping || isOptimizing}
                  className="flex-1 gap-2 font-semibold glow-btn"
                  data-testid="button-start-process"
                >
                  {isCropping ? (
                    <><Loader2 className="w-4 h-4 animate-spin" /> Cropping...</>
                  ) : isOptimizing ? (
                    <><Loader2 className="w-4 h-4 animate-spin" /> Optimizing artwork for print...</>
                  ) : uploadJob.isPending ? (
                    <><Loader2 className="w-4 h-4 animate-spin" /> Uploading...</>
                  ) : (
                    <><UploadCloud className="w-4 h-4" /> Fix Everything</>
                  )}
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={handleClearStaged}
                  disabled={uploadJob.isPending}
                  data-testid="button-clear-staged"
                  title="Start over"
                >
                  <X className="w-4 h-4" />
                </Button>
              </div>
            </div>
          </Card>
        )}

        {showCropTool && (
          <Card className="p-4 glass-card tech-corners" data-testid="panel-crop-tool-content">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Crop className="w-4 h-4 text-amber-600 dark:text-amber-400" />
                <span className="text-sm font-semibold text-foreground">Manual Crop / Mockup Killer</span>
                <span className="text-[10px] text-muted-foreground ml-1">({widthInput}:{heightInput} locked)</span>
              </div>
              <button
                onClick={() => setShowCropTool(false)}
                className="text-muted-foreground hover:text-foreground p-1"
                data-testid="button-close-crop"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            {stagedPreviewUrl ? (
              <ManualCropEmbedded
                sourceImageUrl={stagedPreviewUrl}
                aspectRatio={hasSizeSet ? parseFloat(widthInput) / parseFloat(heightInput) : undefined}
                onCropApply={(coords) => {
                  setPendingCropCoords(coords);
                  setShowCropTool(false);
                  toast({ title: "Crop applied!", description: `Crop region saved at ${widthInput}:${heightInput} aspect ratio.` });
                }}
              />
            ) : (
              <div className="flex items-center justify-center py-8 text-muted-foreground gap-2" data-testid="crop-loading">
                <Loader2 className="w-5 h-5 animate-spin" />
                <span className="text-sm">Loading preview...</span>
              </div>
            )}
          </Card>
        )}
      </div>
      )}

      {wizardStage === 1 && (
        <Card className="p-4 glass-card tech-corners" data-testid="panel-upload-zone">
          <div className="flex items-center gap-2 mb-3">
            <UploadCloud className="w-4 h-4 text-primary" />
            <span className="text-sm font-semibold text-foreground">Stage 1: Upload Artwork</span>
          </div>
          <div
            className={cn(
              "relative overflow-hidden group cursor-pointer transition-all duration-300 border-2 border-dashed rounded-xl bg-card hover-elevate min-h-[260px] flex items-center justify-center scan-lines",
              isDragActive ? "border-primary bg-primary/5" : "border-border/60 hover:border-primary/50",
              isDragReject && "border-destructive bg-destructive/5"
            )}
            {...getRootProps()}
            onMouseEnter={() => setIsHovering(true)}
            onMouseLeave={() => setIsHovering(false)}
          >
            <input {...getInputProps()} data-testid="input-file-upload" />
            <div className="absolute inset-0 bg-grid-pattern opacity-[0.15] pointer-events-none mix-blend-overlay" />
            <div className="relative z-10 flex flex-col items-center justify-center p-6 text-center">
              <div className={cn(
                "w-14 h-14 rounded-full flex items-center justify-center mb-4 transition-all duration-500 shadow-lg",
                isDragActive ? "bg-primary text-primary-foreground scale-110" :
                isDragReject ? "bg-destructive text-destructive-foreground scale-110" :
                "bg-primary/10 text-primary group-hover:scale-110 group-hover:bg-primary group-hover:text-primary-foreground group-hover:shadow-primary/25"
              )}>
                {isDragReject ? (
                  <AlertCircle className="w-7 h-7" />
                ) : isDragActive || isHovering ? (
                  <UploadCloud className="w-7 h-7" />
                ) : (
                  <File className="w-7 h-7" />
                )}
              </div>

              <h3 className="text-lg font-bold font-display tracking-tight text-foreground mb-1" data-testid="text-upload-title">
                {isDragActive ? "Drop to preview" :
                 isDragReject ? "Not supported" :
                 "Drop artwork here"}
              </h3>

              <p className="text-xs text-muted-foreground max-w-[200px] font-medium" data-testid="text-upload-description">
                PDF, JPG, PNG, DOCX, PPTX up to 50MB — drop multiple files for batch processing
              </p>

              <div className="mt-4 flex gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
                <span className="bg-muted px-1.5 py-0.5 rounded">PDF</span>
                <span className="bg-muted px-1.5 py-0.5 rounded">JPG</span>
                <span className="bg-muted px-1.5 py-0.5 rounded">PNG</span>
                <span className="bg-muted px-1.5 py-0.5 rounded">DOCX</span>
              </div>
            </div>
          </div>
        </Card>
      )}

      <Button
        variant="ghost"
        size="sm"
        className="w-full flex items-center justify-center gap-2 text-muted-foreground hover:text-foreground"
        onClick={() => setShowOptions(!showOptions)}
        data-testid="button-toggle-bleed-options"
      >
        <Settings2 className="w-4 h-4" />
        <span className="text-sm font-medium">Bleed & Output Settings</span>
        {showOptions ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
      </Button>

      {showOptions && (
        <Card className="p-6 glass-card space-y-6" data-testid="panel-bleed-options">
          <div className="flex items-center gap-2 mb-2">
            <Settings2 className="w-5 h-5 text-primary" />
            <h3 className="text-lg font-bold text-foreground">Artwork Bleed Adjustment</h3>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-4">
              <div className="space-y-2">
                <Label className="text-sm font-semibold flex items-center gap-2">
                  <Scissors className="w-4 h-4 text-primary" />
                  Bleed Size: {bleedOptions.adjustableBleedSize}mm
                </Label>
                <Slider
                  value={[bleedOptions.adjustableBleedSize]}
                  onValueChange={(v) => updateOption("adjustableBleedSize", v[0])}
                  min={1}
                  max={15}
                  step={0.5}
                  data-testid="slider-bleed-size"
                />
                <p className="text-xs text-muted-foreground">Standard litho bleed is 3-5mm. Adjust for your printer's requirements.</p>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-semibold flex items-center gap-2">
                  <Palette className="w-4 h-4 text-primary" />
                  Color Profile
                </Label>
                <Select value={bleedOptions.colorProfile} onValueChange={(v) => updateOption("colorProfile", v as "cmyk" | "rgb" | "auto")}>
                  <SelectTrigger data-testid="select-color-profile">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cmyk">CMYK (Print)</SelectItem>
                    <SelectItem value="rgb">RGB (Digital)</SelectItem>
                    <SelectItem value="auto">Auto Detect</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-semibold flex items-center gap-2">
                  <Printer className="w-4 h-4 text-primary" />
                  Output Type
                </Label>
                <Select value={bleedOptions.outputType} onValueChange={(v) => updateOption("outputType", v as "print" | "digital")}>
                  <SelectTrigger data-testid="select-output-type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="print">Print (Litho/Offset)</SelectItem>
                    <SelectItem value="digital">Digital (Screen)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-3">
              <Label className="text-sm font-semibold flex items-center gap-2 mb-1">
                <Layers className="w-4 h-4 text-primary" />
                Bleed Handling Options
              </Label>

              <ToggleOption
                id="extendSolidColors"
                label="Extend Solid Colors"
                description="Stretch edge pixels into the bleed zone"
                checked={bleedOptions.extendSolidColors}
                onChange={(v) => updateOption("extendSolidColors", v)}
              />
              <ToggleOption
                id="enableGradientFade"
                label="Gradient Fade"
                description="Fade artwork edges to white in the bleed area"
                checked={bleedOptions.enableGradientFade}
                onChange={(v) => updateOption("enableGradientFade", v)}
              />
              <ToggleOption
                id="sampleEdgeColors"
                label="Sample Edge Colors"
                description="Use edge pixel colors for bleed extension"
                checked={bleedOptions.sampleEdgeColors}
                onChange={(v) => updateOption("sampleEdgeColors", v)}
              />
              <ToggleOption
                id="addBorder"
                label="Add Trim Border"
                description="Draw a thin crop/trim mark border"
                checked={bleedOptions.addBorder}
                onChange={(v) => updateOption("addBorder", v)}
              />
              <ToggleOption
                id="increaseBleedMargins"
                label="Increase Bleed Margins (+2mm)"
                description="Add extra 2mm safety margin to bleed"
                checked={bleedOptions.increaseBleedMargins}
                onChange={(v) => updateOption("increaseBleedMargins", v)}
              />
            </div>
          </div>

          <div className="border-t border-border/40 pt-4">
            <Label className="text-sm font-semibold flex items-center gap-2 mb-3">
              <Grid3X3 className="w-4 h-4 text-primary" />
              Advanced Processing
            </Label>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
              <ToggleOption
                id="separateLayers"
                label="Separate Layers"
                description="Isolate bleed on a separate layer"
                checked={bleedOptions.separateLayers}
                onChange={(v) => updateOption("separateLayers", v)}
              />
              <ToggleOption
                id="useClippingMasks"
                label="Clipping Masks"
                description="Apply clipping masks to bleed edges"
                checked={bleedOptions.useClippingMasks}
                onChange={(v) => updateOption("useClippingMasks", v)}
              />
              <ToggleOption
                id="resizeArtwork"
                label="Resize Artwork"
                description="Scale artwork to fit target size"
                checked={bleedOptions.resizeArtwork}
                onChange={(v) => updateOption("resizeArtwork", v)}
              />
              <ToggleOption
                id="adjustTrimLines"
                label="Adjust Trim Lines"
                description="Recalculate trim marks for new bleed"
                checked={bleedOptions.adjustTrimLines}
                onChange={(v) => updateOption("adjustTrimLines", v)}
              />
              <ToggleOption
                id="useTemplates"
                label="Use Templates"
                description="Apply standard print templates"
                checked={bleedOptions.useTemplates}
                onChange={(v) => updateOption("useTemplates", v)}
              />
              <ToggleOption
                id="createMockups"
                label="Create Mockups"
                description="Generate a print preview mockup"
                checked={bleedOptions.createMockups}
                onChange={(v) => updateOption("createMockups", v)}
              />
            </div>
          </div>

          <div className="border-t border-border/40 pt-4">
            <Label className="text-sm font-semibold flex items-center gap-2 mb-3">
              <Shield className="w-4 h-4 text-primary" />
              Prepress Automation Suite
            </Label>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
              <ToggleOption
                id="autoSafeZoneFix"
                label="Auto Safe Zone Fix"
                description="Detect and flag content too close to trim"
                checked={bleedOptions.autoSafeZoneFix}
                onChange={(v) => updateOption("autoSafeZoneFix", v)}
              />
              <ToggleOption
                id="enableLayoutBalancing"
                label="Layout Balancing"
                description="Detect layout blocks and check balance"
                checked={bleedOptions.enableLayoutBalancing}
                onChange={(v) => updateOption("enableLayoutBalancing", v)}
              />
              <ToggleOption
                id="enableCompositionCenter"
                label="Composition Center"
                description="AI visual weight centroid analysis"
                checked={bleedOptions.enableCompositionCenter}
                onChange={(v) => updateOption("enableCompositionCenter", v)}
              />
              <ToggleOption
                id="enableSmartDownscale"
                label="Smart Downscale"
                description="Last-resort scale advisory (max 85%)"
                checked={bleedOptions.enableSmartDownscale}
                onChange={(v) => updateOption("enableSmartDownscale", v)}
              />
              <ToggleOption
                id="enableMarginNormalization"
                label="Margin Normalization"
                description="Detect uneven content margins"
                checked={bleedOptions.enableMarginNormalization}
                onChange={(v) => updateOption("enableMarginNormalization", v)}
              />
              <ToggleOption
                id="enableToleranceSimulation"
                label="Tolerance Simulation"
                description="Simulate ±1mm trim drift risk"
                checked={bleedOptions.enableToleranceSimulation}
                onChange={(v) => updateOption("enableToleranceSimulation", v)}
              />
              <ToggleOption
                id="enableWhiteEdgeRisk"
                label="White-Edge Risk"
                description="Detect dark edges with thin bleed"
                checked={bleedOptions.enableWhiteEdgeRisk}
                onChange={(v) => updateOption("enableWhiteEdgeRisk", v)}
              />
              <ToggleOption
                id="enablePdfxCompliance"
                label="PDF/X Compliance"
                description="Check PDF/X print standard compliance"
                checked={bleedOptions.enablePdfxCompliance}
                onChange={(v) => updateOption("enablePdfxCompliance", v)}
              />
            </div>
          </div>

          <div className="border-t border-border/40 pt-4">
            <Label className="text-sm font-semibold flex items-center gap-2 mb-3">
              <BookOpen className="w-4 h-4 text-primary" />
              Booklet Processing
            </Label>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
              <ToggleOption
                id="enableSpineShiftDetection"
                label="Spine Shift Detection"
                description="Detect content too close to spine"
                checked={bleedOptions.enableSpineShiftDetection}
                onChange={(v) => updateOption("enableSpineShiftDetection", v)}
              />
              <ToggleOption
                id="enableCreepCompensation"
                label="Creep Compensation"
                description="Calculate inner page outward shift"
                checked={bleedOptions.enableCreepCompensation}
                onChange={(v) => updateOption("enableCreepCompensation", v)}
              />
              <ToggleOption
                id="enableGutterCollisionCheck"
                label="Gutter Collision"
                description="Detect objects overlapping the gutter"
                checked={bleedOptions.enableGutterCollisionCheck}
                onChange={(v) => updateOption("enableGutterCollisionCheck", v)}
              />
            </div>
          </div>

          <div className="flex items-center justify-between pt-2">
            <p className="text-xs text-muted-foreground">
              Active settings: {Object.entries(bleedOptions).filter(([k, v]) => typeof v === 'boolean' && v).length} options enabled
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setBleedOptions({ ...defaultBleedOptions })}
              data-testid="button-reset-options"
            >
              Reset to Defaults
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}

function ToggleOption({ id, label, description, checked, onChange }: {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-start gap-3 p-2 rounded-lg hover:bg-muted/50 transition-colors">
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onChange}
        data-testid={`switch-${id}`}
      />
      <div className="flex-1 min-w-0">
        <Label htmlFor={id} className="text-sm font-medium cursor-pointer leading-tight">{label}</Label>
        <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
      </div>
    </div>
  );
}
