import { useState, useRef, useCallback } from "react";
import { Link } from "wouter";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import {
  ArrowLeft,
  Upload,
  Shrink,
  Download,
  Loader2,
  Eye,
  Info,
  RotateCcw,
} from "lucide-react";

interface PreviewResult {
  previewUrl: string;
  storedFilename: string;
  fileType: string;
  originalSize: [number, number];
  innerSize: [number, number];
  marginPx: [number, number];
  marginMm: [number, number];
  shrinkFactor: number;
}

interface ExecuteResult {
  downloadUrl: string;
  downloadFilename: string;
  shrinkFactor: number;
  pageCount?: number;
  pages?: Array<{
    page: number;
    originalSizeMm: [number, number];
    innerSizeMm: [number, number];
    marginMm: [number, number];
  }>;
  originalSize?: [number, number];
  innerSize?: [number, number];
  marginMm?: [number, number];
}

export default function SafeMarginShrink() {
  const { toast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [shrinkPercent, setShrinkPercent] = useState(92);
  const [isUploading, setIsUploading] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [executed, setExecuted] = useState<ExecuteResult | null>(null);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (!selected) return;

    const ext = selected.name.split(".").pop()?.toLowerCase();
    if (!["pdf", "jpg", "jpeg", "png"].includes(ext || "")) {
      toast({
        title: "Unsupported file",
        description: "Please upload a PDF, JPG, or PNG file.",
        variant: "destructive",
      });
      return;
    }

    if (selected.size > 50 * 1024 * 1024) {
      toast({
        title: "File too large",
        description: "Maximum file size is 50MB.",
        variant: "destructive",
      });
      return;
    }

    setFile(selected);
    setPreview(null);
    setExecuted(null);
  }, [toast]);

  const generatePreview = useCallback(async () => {
    if (!file) return;
    setIsUploading(true);
    setExecuted(null);

    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("shrinkFactor", String(shrinkPercent / 100));

      const resp = await fetch("/api/shrink/preview", {
        method: "POST",
        body: formData,
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.message || "Preview failed");
      }

      const data = await resp.json();
      setPreview(data);
    } catch (err: any) {
      toast({
        title: "Preview failed",
        description: err.message || "Could not generate preview.",
        variant: "destructive",
      });
    } finally {
      setIsUploading(false);
    }
  }, [file, shrinkPercent, toast]);

  const executeShrink = useCallback(async () => {
    if (!preview) return;
    setIsExecuting(true);

    try {
      const resp = await fetch("/api/shrink/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          storedFilename: preview.storedFilename,
          fileType: preview.fileType,
          shrinkFactor: shrinkPercent / 100,
        }),
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.message || "Shrink failed");
      }

      const data = await resp.json();
      setExecuted(data);
      toast({
        title: "Safe margin applied",
        description: "Your artwork is ready to download.",
      });
    } catch (err: any) {
      toast({
        title: "Processing failed",
        description: err.message || "Could not apply safe margins.",
        variant: "destructive",
      });
    } finally {
      setIsExecuting(false);
    }
  }, [preview, shrinkPercent, toast]);

  const resetTool = () => {
    setFile(null);
    setPreview(null);
    setExecuted(null);
    setShrinkPercent(92);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const marginPercent = ((100 - shrinkPercent) / 2).toFixed(1);

  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-4xl mx-auto p-4 md:p-6 space-y-6">
        <div className="flex items-center gap-4">
          <Link href="/">
            <Button variant="ghost" size="icon" data-testid="button-back-home">
              <ArrowLeft className="w-5 h-5" />
            </Button>
          </Link>
          <div>
            <h1 className="text-2xl font-bold tracking-tight" data-testid="text-page-title">
              Safe Margin Shrink
            </h1>
            <p className="text-sm text-muted-foreground">
              Shrink composition inward to create natural bleed margins
            </p>
          </div>
        </div>

        <Card className="p-5 bg-primary/5 border-primary/20" data-testid="card-info">
          <div className="flex gap-3">
            <Info className="w-5 h-5 text-primary shrink-0 mt-0.5" />
            <div className="text-sm space-y-1">
              <p className="font-semibold text-foreground">How it works</p>
              <p className="text-muted-foreground">
                Crops inward from each edge of your artwork, then mirror-extends the trimmed edges
                back out to fill the bleed zone. The inner safe zone is a direct pixel crop of
                the original — zero resampling, full DPI preserved. The bleed area is a seamless
                reflection of the edge content, so colors, lines, and photos flow naturally to the cut edge.
              </p>
              <p className="text-muted-foreground">
                At <strong>92%</strong>, roughly <strong>4%</strong> is cropped from each side and reflected outward — ideal for standard 5mm bleed on A4/A5.
              </p>
            </div>
          </div>
        </Card>

        <Card className="p-6 space-y-5" data-testid="card-upload">
          <div className="space-y-4">
            <div>
              <Label className="text-sm font-semibold mb-2 block">1. Select artwork file</Label>
              <div className="flex items-center gap-3">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.jpg,.jpeg,.png"
                  onChange={handleFileSelect}
                  className="hidden"
                  data-testid="input-file"
                />
                <Button
                  variant="outline"
                  onClick={() => fileInputRef.current?.click()}
                  data-testid="button-select-file"
                >
                  <Upload className="w-4 h-4 mr-2" />
                  {file ? "Change File" : "Choose File"}
                </Button>
                {file && (
                  <span className="text-sm text-muted-foreground truncate max-w-[300px]" data-testid="text-filename">
                    {file.name} ({(file.size / 1024 / 1024).toFixed(1)}MB)
                  </span>
                )}
              </div>
            </div>

            <div className="space-y-2">
              <Label className="text-sm font-semibold flex items-center gap-2">
                <Shrink className="w-4 h-4 text-primary" />
                2. Shrink Factor: {shrinkPercent}%
                <span className="text-xs text-muted-foreground font-normal">
                  ({marginPercent}% margin each side)
                </span>
              </Label>
              <Slider
                value={[shrinkPercent]}
                onValueChange={(v) => setShrinkPercent(v[0])}
                min={50}
                max={99}
                step={1}
                data-testid="slider-shrink-factor"
              />
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>50% (25% margin)</span>
                <span>Recommended: 90-95%</span>
                <span>99% (0.5% margin)</span>
              </div>
            </div>

            <div className="flex gap-3 pt-2">
              <Button
                onClick={generatePreview}
                disabled={!file || isUploading}
                data-testid="button-preview"
              >
                {isUploading ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Generating Preview...
                  </>
                ) : (
                  <>
                    <Eye className="w-4 h-4 mr-2" />
                    Preview
                  </>
                )}
              </Button>

              {preview && (
                <Button
                  variant="outline"
                  onClick={resetTool}
                  data-testid="button-reset"
                >
                  <RotateCcw className="w-4 h-4 mr-2" />
                  Reset
                </Button>
              )}
            </div>
          </div>
        </Card>

        {preview && (
          <Card className="p-6 space-y-5" data-testid="card-preview-result">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold" data-testid="text-preview-title">Preview</h2>
              <div className="flex items-center gap-4 text-sm text-muted-foreground">
                <span data-testid="text-original-size">
                  Original: {preview.originalSize[0]}×{preview.originalSize[1]}px
                </span>
                <span data-testid="text-shrunk-size">
                  Inner: {preview.innerSize[0]}×{preview.innerSize[1]}px
                </span>
                <span data-testid="text-margin-mm">
                  Margin: ~{preview.marginMm[0]}×{preview.marginMm[1]}mm
                </span>
              </div>
            </div>

            <div className="border rounded-lg overflow-hidden bg-muted/30 flex items-center justify-center p-4">
              <img
                src={preview.previewUrl}
                alt="Safe margin preview"
                className="max-w-full max-h-[600px] object-contain rounded"
                data-testid="img-preview"
              />
            </div>

            <div className="flex gap-2 flex-wrap text-xs">
              <span className="flex items-center gap-1.5 px-2 py-1 rounded bg-red-100 dark:bg-red-950 text-red-700 dark:text-red-300">
                <span className="w-3 h-0.5 bg-red-500 inline-block"></span>
                Full canvas / cut edge
              </span>
              <span className="flex items-center gap-1.5 px-2 py-1 rounded bg-green-100 dark:bg-green-950 text-green-700 dark:text-green-300">
                <span className="w-3 h-0.5 bg-green-500 inline-block"></span>
                Inner safe zone (original pixels)
              </span>
              <span className="flex items-center gap-1.5 px-2 py-1 rounded bg-red-50 dark:bg-red-950/50 text-red-600 dark:text-red-400">
                <span className="w-3 h-3 bg-red-200/60 inline-block rounded-sm"></span>
                Bleed zone (mirror-extended)
              </span>
            </div>

            <div className="flex gap-3 pt-2 border-t border-border/40">
              <Button
                onClick={executeShrink}
                disabled={isExecuting}
                data-testid="button-apply"
              >
                {isExecuting ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Applying...
                  </>
                ) : (
                  <>
                    <Shrink className="w-4 h-4 mr-2" />
                    Apply & Generate File
                  </>
                )}
              </Button>
            </div>
          </Card>
        )}

        {executed && (
          <Card className="p-6 space-y-4 border-green-500/30 bg-green-50/50 dark:bg-green-950/20" data-testid="card-download">
            <h2 className="text-lg font-bold text-green-700 dark:text-green-400" data-testid="text-download-title">
              Artwork Ready
            </h2>

            {executed.pages && executed.pages.length > 0 ? (
              <div className="space-y-2">
                {executed.pages.map((p) => (
                  <div key={p.page} className="text-sm text-muted-foreground" data-testid={`text-page-info-${p.page}`}>
                    Page {p.page}: {p.originalSizeMm[0]}×{p.originalSizeMm[1]}mm →
                    Inner {p.innerSizeMm[0]}×{p.innerSizeMm[1]}mm
                    (margin: {p.marginMm[0]}×{p.marginMm[1]}mm)
                  </div>
                ))}
              </div>
            ) : executed.marginMm ? (
              <p className="text-sm text-muted-foreground" data-testid="text-result-info">
                Shrink: {Math.round((executed.shrinkFactor || 0) * 100)}% |
                Margin: ~{executed.marginMm[0]}×{executed.marginMm[1]}mm
              </p>
            ) : null}

            <a href={executed.downloadUrl} download={executed.downloadFilename}>
              <Button className="mt-2" data-testid="button-download">
                <Download className="w-4 h-4 mr-2" />
                Download {executed.downloadFilename}
              </Button>
            </a>
          </Card>
        )}
      </div>
    </div>
  );
}
