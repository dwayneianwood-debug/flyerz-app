import { useRoute } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Loader2, Sparkles, Eye } from "lucide-react";

type JobData = {
  id: number;
  filename: string;
  auditResults?: {
    proofPath?: string;
    bleedProofPath?: string;
    compiledPdfPath?: string;
    compiledStrategy?: string;
  };
};

export default function ArProof() {
  const [, params] = useRoute("/ar-proof/:jobId");
  const jobId = params?.jobId;
  const [finish, setFinish] = useState<"glossy" | "matte">("glossy");
  const [modelViewerLoaded, setModelViewerLoaded] = useState(false);

  const { data: job, isLoading, error } = useQuery<JobData>({
    queryKey: ["job", jobId],
    enabled: !!jobId,
  });

  useEffect(() => {
    if (customElements.get("model-viewer")) {
      setModelViewerLoaded(true);
      return;
    }
    const script = document.createElement("script");
    script.type = "module";
    script.src = "https://ajax.googleapis.com/ajax/libs/model-viewer/3.4.0/model-viewer.min.js";
    script.onload = () => setModelViewerLoaded(true);
    document.head.appendChild(script);
    return () => {
      try { document.head.removeChild(script); } catch {}
    };
  }, []);

  const proofUrl = job?.auditResults?.bleedProofPath || job?.auditResults?.proofPath || "";
  const textureUrl = proofUrl ? `/api/file/${encodeURIComponent(proofUrl)}` : "";

  const aspectW = 210;
  const aspectH = 297;
  const planeW = 0.21;
  const planeH = planeW * (aspectH / aspectW);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background" data-testid="ar-proof-loading">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4" data-testid="ar-proof-error">
        <Card className="p-6 text-center max-w-sm">
          <p className="text-destructive font-semibold">Could not load artwork</p>
          <p className="text-sm text-muted-foreground mt-2">This proof link may have expired or the job does not exist.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-black text-white" data-testid="ar-proof-page">
      <div className="max-w-lg mx-auto px-4 py-6">
        <div className="text-center mb-6">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/10 backdrop-blur mb-3">
            <Eye className="w-4 h-4 text-violet-400" />
            <span className="text-xs font-semibold tracking-wider uppercase">AR Print Preview</span>
          </div>
          <h1 className="text-xl font-bold" data-testid="text-ar-title">
            {job.filename}
          </h1>
          <p className="text-sm text-gray-400 mt-1">Hold your phone up and tap to place the flyer in your space</p>
        </div>

        <div className="rounded-2xl overflow-hidden bg-gray-800/50 border border-white/10" style={{ aspectRatio: "3/4" }}>
          {modelViewerLoaded && textureUrl ? (
            <div
              className="w-full h-full"
              data-testid="ar-model-container"
              dangerouslySetInnerHTML={{
                __html: `
                  <model-viewer
                    id="flyerViewer"
                    style="width:100%;height:100%;background:transparent;"
                    camera-controls
                    ar
                    ar-modes="webxr scene-viewer quick-look"
                    poster=""
                    shadow-intensity="1"
                    environment-image="neutral"
                    exposure="1"
                    tone-mapping="neutral"
                  >
                    <div slot="poster" style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;background:#1a1a2e;">
                      <p style="color:#888;font-size:14px;">Loading 3D preview...</p>
                    </div>
                  </model-viewer>
                `,
              }}
            />
          ) : (
            <div className="w-full h-full flex flex-col items-center justify-center gap-3 p-8">
              {textureUrl ? (
                <>
                  <img
                    src={textureUrl}
                    alt="Print preview"
                    className="max-w-full max-h-[60%] object-contain rounded-lg shadow-xl"
                    data-testid="ar-flat-preview"
                  />
                  <p className="text-xs text-gray-400 text-center mt-2">
                    3D viewer loading... Your proof is shown above as a flat preview.
                  </p>
                </>
              ) : (
                <p className="text-gray-400 text-sm" data-testid="ar-no-proof">No proof image available</p>
              )}
            </div>
          )}
        </div>

        <div className="mt-5 flex gap-3" data-testid="ar-finish-controls">
          <Button
            variant={finish === "glossy" ? "default" : "outline"}
            className={`flex-1 gap-2 ${finish === "glossy" ? "bg-violet-600 hover:bg-violet-700 text-white" : "border-white/20 text-white hover:bg-white/10"}`}
            onClick={() => {
              setFinish("glossy");
              const viewer = document.getElementById("flyerViewer") as any;
              if (viewer?.model) {
                try {
                  const mat = viewer.model.materials[0];
                  if (mat) {
                    mat.pbrMetallicRoughness.setRoughnessFactor(0.15);
                    mat.pbrMetallicRoughness.setMetalnessFactor(0.3);
                  }
                } catch {}
              }
            }}
            data-testid="button-glossy"
          >
            <Sparkles className="w-4 h-4" />
            Glossy
          </Button>
          <Button
            variant={finish === "matte" ? "default" : "outline"}
            className={`flex-1 gap-2 ${finish === "matte" ? "bg-gray-600 hover:bg-gray-700 text-white" : "border-white/20 text-white hover:bg-white/10"}`}
            onClick={() => {
              setFinish("matte");
              const viewer = document.getElementById("flyerViewer") as any;
              if (viewer?.model) {
                try {
                  const mat = viewer.model.materials[0];
                  if (mat) {
                    mat.pbrMetallicRoughness.setRoughnessFactor(0.85);
                    mat.pbrMetallicRoughness.setMetalnessFactor(0.0);
                  }
                } catch {}
              }
            }}
            data-testid="button-matte"
          >
            <Eye className="w-4 h-4" />
            Matte
          </Button>
        </div>

        <div className="mt-4 p-3 rounded-lg bg-white/5 border border-white/10 text-center">
          <p className="text-xs text-gray-400">
            <span className="font-semibold text-gray-300">Finish: </span>
            <span data-testid="text-finish-label">
              {finish === "glossy" ? "Glossy — High shine, vibrant colours" : "Matte — Soft touch, no glare"}
            </span>
          </p>
        </div>

        <p className="text-[10px] text-gray-500 text-center mt-4">
          All rendering happens on your device. No server processing required.
        </p>
      </div>
    </div>
  );
}
