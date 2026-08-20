import { Layout } from "@/components/layout";
import { Link } from "wouter";
import { motion } from "framer-motion";
import {
  Shield,
  Radar,
  Droplets,
  Cpu,
  Sparkles,
  FileDown,
  ArrowLeft,
  Activity,
} from "lucide-react";
import { Button } from "@/components/ui/button";

const PHASES = [
  {
    title: "Phase 1: The Non-Destructive Core",
    icon: Shield,
    accent: "#4ade80",
    bullets: [
      "Zero-destructive sizing: we never squash artwork on independent X/Y axes. Cover scaling + centre crop preserves a 1:1 pixel fidelity discipline on the trim plate.",
      "50 MB memory leash: uncompressed arrays above the production cap fail fast with ArtworkMemoryLimitError so the press queue never inherits silent downscales.",
      "Vector preservation: bad PDF geometry is repaired by redefining MediaBox / CropBox / TrimBox in PyMuPDF—no opportunistic rasterize-to-fix shortcut.",
    ],
  },
  {
    title: "Phase 2: Safe Zone Intelligence",
    icon: Radar,
    accent: "#38bdf8",
    bullets: [
      "Shrink & Re-Bleed auto-heal: validate_safe_zone() watches the inner 3.0 mm strips; violations trigger auto_resolve_safe_zone() from img_bgr.copy(), 30 px/side INTER_AREA shrink, +30 px bleed budget, then your chosen strategy (including AI Outpaint).",
      "Canny radar: 30 px outer bands run high-frequency edge-density scoring to spotlight typography and logo energy before trimming.",
    ],
  },
  {
    title: "Phase 3: Smart Bleed Engine",
    icon: Droplets,
    accent: "#00cc88",
    bullets: [
      "AI Outpaint (Melt): Content-Aware Abstraction reflects edge pixels, Gaussian-melts the seam, feathers alpha, and seeds litho-grain so extensions read like real stock.",
      "Pixel-Drift uses a 1-pixel sampling radius on stretch / edge-replication paths—no backward glyphs, no kaleidoscope halos.",
    ],
  },
];

function ShrinkBleedDiagram() {
  return (
    <div
      className="mt-8 p-6 rounded-xl border border-white/10 bg-[#16213e]"
      data-testid="shrink-bleed-diagram"
    >
      <div className="flex items-center gap-2 mb-4 text-[#4ade80] text-sm font-bold tracking-wide uppercase">
        <Activity className="w-4 h-4" />
        Shrink & Re-Bleed motion
      </div>
      <p className="text-xs text-[#8891b0] mb-5">
        validate_safe_zone hit → img.copy() → INTER_AREA micro-shrink (30 px/side) → bleed_px_effective = base + 30 →
        bleed engine re-runs → litho receives a full halo again.
      </p>
      <div className="flex flex-col sm:flex-row items-stretch gap-4 justify-center">
        <div className="flex-1 rounded-lg border border-red-500/40 bg-[#2a1a24] p-4 min-h-[120px]">
          <p className="text-[11px] font-bold text-[#fca5a5] mb-2">Before</p>
          <p className="text-xs text-[#b8c1ec] leading-relaxed">
            Trim canvas locked • live type hugging the danger band • risk on guillotine drift.
          </p>
        </div>
        <div className="hidden sm:flex flex-col items-center justify-center text-[#38bdf8]">
          <span className="text-2xl leading-none">→</span>
          <span className="text-[10px] uppercase tracking-widest mt-1 text-[#64748b]">Auto-heal</span>
        </div>
        <div className="flex-1 rounded-lg border border-emerald-500/40 bg-[#0f2840] p-4 min-h-[120px]">
          <p className="text-[11px] font-bold text-[#4ade80] mb-2">After</p>
          <p className="text-xs text-[#b8c1ec] leading-relaxed">
            Tighter content island • extended bleed • edge melt / strategy fill restores press-safe
            coverage.
          </p>
        </div>
      </div>
    </div>
  );
}

export default function DashboardRules() {
  return (
    <Layout>
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45 }}
        className="max-w-4xl mx-auto pb-12"
      >
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
            <Link href="/" data-testid="link-back-home">
              <ArrowLeft className="w-4 h-4 mr-1" />
              Dashboard
            </Link>
          </Button>
        </div>

        <div className="audit-container" data-testid="rules-intelligence-panel">
          <p className="text-xs uppercase tracking-[0.35em] text-[#4ade80] font-semibold mb-2">
            System Intelligence
          </p>
          <h1 className="text-2xl sm:text-3xl font-black text-[#e6e6fa] mb-2 tracking-tight">
            25-Point Check — How the engine thinks
          </h1>
          <p className="text-[#b8c1ec] text-sm sm:text-base mb-6 leading-relaxed max-w-3xl">
            Same{" "}
            <span className="text-white font-semibold">25-point litho audit</span> you see inside the
            Artwork Intelligence Report—delivered as live automation: Radar, Auto-Heal shrink/bleed,
            melt-based AI outpaint, and vector-safe PDF surgery.
          </p>

          <div className="flex flex-wrap gap-3 mb-8">
            <Button
              asChild
              className="gap-2 bg-primary hover:bg-primary/90 shadow-lg shadow-primary/30"
              data-testid="download-checks-guide"
            >
              <a href="/api/checks-guide" download>
                <FileDown className="w-4 h-4" />
                Download PDF guide
              </a>
            </Button>
            <Button variant="outline" asChild className="border-[#334155] text-[#e6e6fa] bg-transparent">
              <Link href="/">Return to upload wizard</Link>
            </Button>
          </div>

          <div className="grid gap-5">
            {PHASES.map((phase, idx) => {
              const Icon = phase.icon;
              return (
                <div
                  key={phase.title}
                  className="audit-card text-left"
                  style={{ borderLeftColor: phase.accent }}
                  data-testid={`phase-card-${idx}`}
                >
                  <div className="flex items-start gap-3 mb-3">
                    <div
                      className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
                      style={{ background: `${phase.accent}22` }}
                    >
                      <Icon className="w-5 h-5" style={{ color: phase.accent }} />
                    </div>
                    <h2 className="text-lg font-bold text-[#e6e6fa] m-0 leading-tight">{phase.title}</h2>
                  </div>
                  <ul className="space-y-2 pl-1 list-none m-0">
                    {phase.bullets.map((b, bi) => (
                      <li
                        key={`${idx}-${bi}`}
                        className="flex gap-2 text-sm text-[#b8c1ec] leading-relaxed"
                      >
                        <Cpu className="w-3.5 h-3.5 mt-0.5 shrink-0 opacity-60" />
                        <span>{b}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>

          <ShrinkBleedDiagram />

          <div className="mt-8 flex items-center gap-2 text-xs text-[#64748b]">
            <Sparkles className="w-3.5 h-3.5 text-[#38bdf8]" />
            Dark console styling matches the live Artwork Intelligence Report panels.
          </div>
        </div>
      </motion.div>
    </Layout>
  );
}
