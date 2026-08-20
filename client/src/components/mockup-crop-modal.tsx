import { useState, useRef, useCallback, useEffect } from "react";
import ReactCrop, { type Crop, type PixelCrop, centerCrop, makeAspectCrop } from "react-image-crop";
import "react-image-crop/dist/ReactCrop.css";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Crop as CropIcon, X, Check, RotateCcw, Crosshair, Lock } from "lucide-react";

interface MockupCropModalProps {
  imageSrc: string;
  onApply: (crop: { cropX: number; cropY: number; cropWidth: number; cropHeight: number }) => void;
  onCancel: () => void;
  targetWidthMm?: number;
  targetHeightMm?: number;
}

export default function MockupCropModal({ imageSrc, onApply, onCancel, targetWidthMm, targetHeightMm }: MockupCropModalProps) {
  const [crop, setCrop] = useState<Crop>();
  const [completedCrop, setCompletedCrop] = useState<PixelCrop>();
  const imgRef = useRef<HTMLImageElement>(null);
  const [naturalSize, setNaturalSize] = useState({ w: 0, h: 0 });
  const [isMobile, setIsMobile] = useState(false);

  const targetAspect = (targetWidthMm && targetHeightMm && targetWidthMm > 0 && targetHeightMm > 0)
    ? targetWidthMm / targetHeightMm
    : undefined;

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 640);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, []);

  const createCenteredCrop = useCallback((imgWidth: number, imgHeight: number, coverPercent: number = 80) => {
    if (targetAspect) {
      const aspectCrop = makeAspectCrop(
        { unit: "%", width: coverPercent },
        targetAspect,
        imgWidth,
        imgHeight
      );
      return centerCrop(aspectCrop, imgWidth, imgHeight);
    }
    return {
      unit: "%" as const,
      x: (100 - coverPercent) / 2,
      y: (100 - coverPercent) / 2,
      width: coverPercent,
      height: coverPercent,
    };
  }, [targetAspect]);

  const onImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const { naturalWidth, naturalHeight, width, height } = e.currentTarget;
    setNaturalSize({ w: naturalWidth, h: naturalHeight });
    const defaultCrop = createCenteredCrop(width, height);
    setCrop(defaultCrop);
  }, [createCenteredCrop]);

  const handleCenterCrop = () => {
    if (!imgRef.current) return;
    const { width, height } = imgRef.current;

    let widthPct = 80;
    if (crop && completedCrop && imgRef.current) {
      widthPct = (completedCrop.width / width) * 100;
      widthPct = Math.max(10, Math.min(95, widthPct));
    } else if (crop?.unit === "%" && crop.width) {
      widthPct = crop.width;
    }
    const centeredCrop = createCenteredCrop(width, height, widthPct);
    setCrop(centeredCrop);
    setCompletedCrop(undefined);
  };

  const handleApply = () => {
    if (!completedCrop || !imgRef.current) return;

    const img = imgRef.current;
    const scaleX = naturalSize.w / img.width;
    const scaleY = naturalSize.h / img.height;

    const pixelCrop = {
      cropX: Math.round(completedCrop.x * scaleX),
      cropY: Math.round(completedCrop.y * scaleY),
      cropWidth: Math.round(completedCrop.width * scaleX),
      cropHeight: Math.round(completedCrop.height * scaleY),
    };

    onApply(pixelCrop);
  };

  const handleReset = () => {
    if (!imgRef.current) return;
    const { width, height } = imgRef.current;
    setCrop(createCenteredCrop(width, height, 90));
    setCompletedCrop(undefined);
  };

  const displayDims = completedCrop && imgRef.current
    ? {
        w: Math.round(completedCrop.width * (naturalSize.w / imgRef.current.width)),
        h: Math.round(completedCrop.height * (naturalSize.h / imgRef.current.height)),
      }
    : null;

  const maxImgHeight = isMobile ? "40vh" : "55vh";

  const targetLabel = (targetWidthMm && targetHeightMm)
    ? `${targetWidthMm} × ${targetHeightMm} mm`
    : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/70 p-0 sm:p-4"
      data-testid="mockup-crop-overlay"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <Card className="w-full sm:max-w-3xl max-h-[95vh] sm:max-h-[90vh] overflow-auto p-4 sm:p-6 bg-background rounded-t-2xl sm:rounded-xl">
        <div className="flex items-center justify-between mb-3 sm:mb-4">
          <div className="flex items-center gap-2">
            <CropIcon className="w-5 h-5 text-primary" />
            <h2 className="text-base sm:text-lg font-bold font-display">
              {isMobile ? "Define Artwork Edges" : "Mockup Killer — Define Artwork Edges"}
            </h2>
          </div>
          <Button variant="ghost" size="icon" onClick={onCancel} data-testid="button-crop-cancel">
            <X className="w-4 h-4" />
          </Button>
        </div>

        <p className="text-xs sm:text-sm text-muted-foreground mb-2 sm:mb-3">
          {isMobile
            ? "Pinch & drag the handles to select the actual flyer area."
            : "Drag the corners to select the actual flyer area. This tells the engine exactly where your artwork starts and ends, bypassing automatic detection."}
        </p>

        {targetAspect && (
          <div className="flex items-center gap-1.5 mb-3 px-2 py-1.5 bg-primary/5 dark:bg-primary/10 border border-primary/20 rounded-md w-fit" data-testid="aspect-lock-badge">
            <Lock className="w-3 h-3 text-primary" />
            <span className="text-xs font-medium text-primary">
              Aspect ratio locked to {targetLabel} ({targetAspect.toFixed(3)})
            </span>
          </div>
        )}

        <div className="flex gap-2 mb-3">
          <Button
            variant="outline"
            size="sm"
            onClick={handleCenterCrop}
            className="gap-1.5 text-xs"
            data-testid="button-center-crop"
          >
            <Crosshair className="w-3.5 h-3.5" />
            Center Crop Box
          </Button>
        </div>

        <div
          className="flex justify-center mb-3 sm:mb-4 bg-muted/30 rounded-lg p-1 sm:p-2 overflow-hidden"
          style={{ touchAction: "none" }}
        >
          <ReactCrop
            crop={crop}
            onChange={(c) => setCrop(c)}
            onComplete={(c) => setCompletedCrop(c)}
            aspect={targetAspect}
            style={{ maxHeight: maxImgHeight, touchAction: "none" }}
          >
            <img
              ref={imgRef}
              src={imageSrc}
              onLoad={onImageLoad}
              alt="Crop preview"
              draggable={false}
              style={{
                maxHeight: maxImgHeight,
                width: "auto",
                touchAction: "none",
                userSelect: "none",
                WebkitUserSelect: "none",
              }}
              crossOrigin="anonymous"
              data-testid="img-crop-preview"
            />
          </ReactCrop>
        </div>

        {displayDims && (
          <p className="text-xs text-muted-foreground text-center mb-3 sm:mb-4" data-testid="text-crop-dimensions">
            Selected area: {displayDims.w} × {displayDims.h} px
            {targetAspect && ` (ratio: ${(displayDims.w / displayDims.h).toFixed(3)})`}
          </p>
        )}

        <div className="flex flex-col sm:flex-row justify-between items-stretch sm:items-center gap-2">
          <Button variant="outline" onClick={handleReset} className="order-2 sm:order-1" data-testid="button-crop-reset">
            <RotateCcw className="w-4 h-4 mr-2" />
            Reset
          </Button>
          <div className="flex gap-2 order-1 sm:order-2">
            <Button variant="outline" onClick={onCancel} className="flex-1 sm:flex-none" data-testid="button-crop-dismiss">
              Cancel
            </Button>
            <Button
              onClick={handleApply}
              disabled={!completedCrop}
              className="flex-1 sm:flex-none"
              data-testid="button-crop-apply"
            >
              <Check className="w-4 h-4 mr-2" />
              Apply Crop
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
