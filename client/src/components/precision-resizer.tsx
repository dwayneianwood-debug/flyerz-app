import { useState, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/hooks/use-toast";
import { Maximize2, Upload, Download, AlertTriangle, Loader2, CheckCircle2, FileImage, Palette, Sparkles } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import type { ResizeAudit } from "@shared/schema";

interface LogEntry {
  step: string;
  status: string;
  message: string;
}

interface ResizeResult {
  success: boolean;
  downloadUrl: string;
  downloadFilename: string;
  originalWidth: number;
  originalHeight: number;
  targetWidth: number;
  targetHeight: number;
  scalePercent: number;
  method: string;
  cmykMethod?: string;
  dpi?: number;
  colorMode?: string;
  pages?: number;
  logs?: LogEntry[];
  resizeAudit?: ResizeAudit;
}

export function PrecisionResizer() {
  const [file, setFile] = useState<File | null>(null);
  const [targetWidth, setTargetWidth] = useState("");
  const [targetHeight, setTargetHeight] = useState("");
  const [uniform, setUniform] = useState(true);
  const [dragActive, setDragActive] = useState(false);
  const [result, setResult] = useState<ResizeResult | null>(null);
  const { toast } = useToast();

  const scaleWarning = (() => {
    if (!result) return false;
    return result.scalePercent > 200;
  })();

  const resizeMutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("No file selected");

      const formData = new FormData();
      formData.append("file", file);
      formData.append("targetWidth", targetWidth);
      formData.append("targetHeight", targetHeight);
      formData.append("uniform", uniform ? "1" : "0");

      const res = await fetch("/api/resize", {
        method: "POST",
        body: formData,
        credentials: "include",
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.message || "Resize failed");
      }

      return res.json() as Promise<ResizeResult>;
    },
    onSuccess: (data) => {
      setResult(data);
      toast({
        title: "Print-Ready PDF generated",
        description: `Scaled to ${data.targetWidth} x ${data.targetHeight}mm${data.cmykMethod ? " with CMYK conversion" : ""}`,
      });
      if (data.resizeAudit) {
        window.dispatchEvent(new CustomEvent("glitchy:resize-complete", {
          detail: {
            falseMargins: data.resizeAudit.falseMargins,
            scalePercentage: data.resizeAudit.scalePercentage,
            aiUpscaled: data.resizeAudit.aiUpscaled,
          },
        }));
      }
    },
    onError: (error: Error) => {
      toast({
        title: "Processing failed",
        description: error.message,
        variant: "destructive",
      });
    },
  });

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const droppedFile = e.dataTransfer.files?.[0];
    if (droppedFile) {
      const ext = droppedFile.name.split(".").pop()?.toLowerCase();
      if (["pdf", "jpg", "jpeg", "png"].includes(ext || "")) {
        setFile(droppedFile);
        setResult(null);
      } else {
        toast({
          title: "Unsupported file",
          description: "Only PDF, JPG, and PNG files can be resized.",
          variant: "destructive",
        });
      }
    }
  }, [toast]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile) {
      setFile(selectedFile);
      setResult(null);
    }
  }, []);

  const widthNum = parseFloat(targetWidth);
  const heightNum = parseFloat(targetHeight);
  const canSubmit = file && widthNum > 0 && heightNum > 0 && !resizeMutation.isPending;

  return (
    <div className="space-y-5" data-testid="precision-resizer-card">
        <div
          className={`relative border-2 border-dashed rounded-xl p-6 text-center transition-colors cursor-pointer ${
            dragActive
              ? "border-primary bg-primary/5"
              : file
              ? "border-green-500/40 bg-green-50/50 dark:bg-green-950/20"
              : "border-muted-foreground/20 hover:border-primary/40"
          }`}
          onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
          onClick={() => document.getElementById("resize-file-input")?.click()}
          data-testid="resize-dropzone"
        >
          <input
            id="resize-file-input"
            type="file"
            accept=".pdf,.jpg,.jpeg,.png"
            onChange={handleFileSelect}
            className="hidden"
            data-testid="resize-file-input"
          />
          {file ? (
            <div className="flex items-center justify-center gap-2">
              <FileImage className="w-5 h-5 text-green-600" />
              <span className="font-medium text-sm" data-testid="resize-filename">{file.name}</span>
              <span className="text-xs text-muted-foreground">({(file.size / 1024).toFixed(0)} KB)</span>
            </div>
          ) : (
            <div className="space-y-1">
              <Upload className="w-8 h-8 mx-auto text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">Drop a file here or click to select</p>
              <p className="text-xs text-muted-foreground/60">PDF, JPG, PNG</p>
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="resize-width" className="text-sm font-medium">Target Width (mm)</Label>
            <Input
              id="resize-width"
              type="number"
              min="1"
              max="3000"
              step="0.1"
              placeholder="e.g. 210"
              value={targetWidth}
              onChange={(e) => setTargetWidth(e.target.value)}
              data-testid="input-resize-width"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="resize-height" className="text-sm font-medium">Target Height (mm)</Label>
            <Input
              id="resize-height"
              type="number"
              min="1"
              max="3000"
              step="0.1"
              placeholder="e.g. 297"
              value={targetHeight}
              onChange={(e) => setTargetHeight(e.target.value)}
              data-testid="input-resize-height"
            />
          </div>
        </div>

        <div className="flex items-center justify-between rounded-lg border p-3">
          <div>
            <Label htmlFor="uniform-toggle" className="text-sm font-medium cursor-pointer">
              Uniform Scaling (Maintain Proportions)
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              {uniform ? "Fits into target size preserving aspect ratio" : "Stretches to fill exact dimensions"}
            </p>
          </div>
          <Switch
            id="uniform-toggle"
            checked={uniform}
            onCheckedChange={setUniform}
            data-testid="switch-uniform-scaling"
          />
        </div>

        <Button
          className="w-full font-semibold"
          disabled={!canSubmit}
          onClick={() => resizeMutation.mutate()}
          data-testid="button-resize-submit"
        >
          {resizeMutation.isPending ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              Processing...
            </>
          ) : (
            <>
              <Maximize2 className="w-4 h-4 mr-2" />
              Scale &amp; Convert to Print-Ready PDF
            </>
          )}
        </Button>

        <AnimatePresence>
          {result && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="space-y-3"
            >
              {scaleWarning && (
                <div className="flex items-start gap-2 p-3 rounded-lg bg-yellow-50 dark:bg-yellow-950/30 border border-yellow-200 dark:border-yellow-800" data-testid="warning-scale-200">
                  <AlertTriangle className="w-4 h-4 text-yellow-600 mt-0.5 shrink-0" />
                  <p className="text-sm text-yellow-700 dark:text-yellow-400">
                    Warning: Scaling beyond 200% may reduce print sharpness. Current scale: {result.scalePercent.toFixed(1)}%.
                  </p>
                </div>
              )}

              {result.logs && result.logs.length > 0 && (
                <div className="rounded-lg border bg-muted/30 p-3 space-y-1.5" data-testid="resize-status-log">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Processing Log</span>
                  {result.logs.map((log, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <CheckCircle2 className="w-3.5 h-3.5 text-green-600 shrink-0" />
                      <span className="font-medium text-foreground">{log.step}:</span>
                      <span className="text-muted-foreground" data-testid={`log-${log.step.toLowerCase().replace(/\s+/g, '-')}`}>{log.message}</span>
                    </div>
                  ))}
                </div>
              )}

              <div className="rounded-lg border bg-card p-4 space-y-2" data-testid="resize-result">
                <div className="flex items-center gap-2 mb-3">
                  <CheckCircle2 className="w-4 h-4 text-green-600" />
                  <span className="font-medium text-sm">Print-Ready File Generated</span>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <span className="text-muted-foreground">Original:</span>
                  <span className="font-mono" data-testid="text-original-dims">{result.originalWidth} x {result.originalHeight} mm</span>
                  <span className="text-muted-foreground">Output:</span>
                  <span className="font-mono" data-testid="text-output-dims">{result.targetWidth} x {result.targetHeight} mm</span>
                  <span className="text-muted-foreground">Scale:</span>
                  <span className="font-mono" data-testid="text-scale-percent">{result.scalePercent.toFixed(1)}%</span>
                  <span className="text-muted-foreground">Scaling Method:</span>
                  <span className="font-mono text-xs" data-testid="text-method">{result.method}</span>
                  {result.cmykMethod && (
                    <>
                      <span className="text-muted-foreground">Color Conversion:</span>
                      <span className="font-mono text-xs flex items-center gap-1" data-testid="text-cmyk-method">
                        <Palette className="w-3 h-3 text-primary" />
                        {result.cmykMethod}
                      </span>
                    </>
                  )}
                  {result.colorMode && (
                    <>
                      <span className="text-muted-foreground">Color Space:</span>
                      <span className="font-mono" data-testid="text-color-mode">{result.colorMode}</span>
                    </>
                  )}
                  {result.dpi && (
                    <>
                      <span className="text-muted-foreground">DPI:</span>
                      <span className="font-mono">{result.dpi}</span>
                    </>
                  )}
                  {result.pages && (
                    <>
                      <span className="text-muted-foreground">Pages:</span>
                      <span className="font-mono">{result.pages}</span>
                    </>
                  )}
                </div>
                <a
                  href={result.downloadUrl}
                  download
                  className="inline-flex items-center gap-2 mt-3 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90 transition-colors"
                  data-testid="link-download-resized"
                >
                  <Download className="w-4 h-4" />
                  Download Print-Ready PDF
                </a>
              </div>

              {result.resizeAudit && (
                <div className="rounded-xl border-2 border-border/40 bg-white/60 dark:bg-background/40 overflow-hidden" data-testid="resize-audit-card">
                  <div className="px-4 py-3 border-b border-border/30 flex flex-wrap items-center gap-2">
                    <Sparkles className="w-4 h-4 text-primary" />
                    <span className="text-sm font-bold text-foreground">Precision Resize Report</span>
                    {result.resizeAudit.aiUpscaled && (
                      <span className="ml-auto px-2.5 py-1 text-[10px] font-black uppercase tracking-wider rounded-full bg-green-500/15 text-green-600 dark:text-green-400 border border-green-500/30" data-testid="badge-ai-enhanced">
                        AI Enhanced
                      </span>
                    )}
                  </div>
                  <div className="divide-y divide-border/20">
                    <div className="grid grid-cols-2 gap-4 px-5 py-4" data-testid="audit-row-metrics">
                      <div className="flex flex-col gap-1">
                        <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">Scaling Factor</span>
                        <span className={`text-xl font-black ${result.resizeAudit.scalePercentage > 200 ? 'text-amber-500' : 'text-foreground'}`} data-testid="text-audit-scale">
                          {result.resizeAudit.scalePercentage.toFixed(1)}%
                        </span>
                      </div>
                      <div className="flex flex-col gap-1">
                        <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">Method</span>
                        <span className="text-xl font-black text-foreground" data-testid="text-audit-method">
                          {result.resizeAudit.aiUpscaled ? 'AI Reconstruction' : 'Lanczos Sharp'}
                        </span>
                      </div>
                    </div>
                    {result.resizeAudit.aspectRatioWarning && (
                      <div className={`px-5 py-4 ${(result.resizeAudit.cropLossPercent ?? 0) > 10 ? 'bg-yellow-50/90 dark:bg-yellow-500/10' : 'bg-amber-50/80 dark:bg-amber-500/10'}`} data-testid="audit-litho-fill">
                        <div className="flex flex-wrap items-start gap-2">
                          <AlertTriangle className={`w-4 h-4 shrink-0 mt-0.5 ${(result.resizeAudit.cropLossPercent ?? 0) > 10 ? 'text-yellow-600' : 'text-amber-600'}`} />
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">Fit Strategy</span>
                            </div>
                            <span className={`text-sm font-black ${(result.resizeAudit.cropLossPercent ?? 0) > 10 ? 'text-yellow-600 dark:text-yellow-400' : 'text-amber-600 dark:text-amber-400'}`} data-testid="text-audit-fit-strategy">
                              Litho-Fill (No White Gaps)
                            </span>
                            <p className={`text-xs mt-1 ${(result.resizeAudit.cropLossPercent ?? 0) > 10 ? 'text-yellow-700/80 dark:text-yellow-400/70' : 'text-amber-700/80 dark:text-amber-400/70'}`} data-testid="text-audit-crop-loss">
                              {(result.resizeAudit.cropLossPercent ?? 0) > 10
                                ? 'Significant cropping occurred to fit this ratio without white gaps.'
                                : `${(result.resizeAudit.cropLossPercent ?? 0).toFixed(1)}% of artwork overflowed & cropped`
                              }
                            </p>
                          </div>
                        </div>
                      </div>
                    )}
                    {result.resizeAudit.falseMargins && (
                      <div className="px-5 py-3 flex flex-wrap items-center gap-2 bg-blue-50/80 dark:bg-blue-500/10" data-testid="audit-false-margins">
                        <CheckCircle2 className="w-3.5 h-3.5 text-blue-600 shrink-0" />
                        <span className="text-xs text-blue-700 dark:text-blue-400 font-medium">False margins detected and stripped before resizing</span>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
    </div>
  );
}
