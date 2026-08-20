import { ReactNode, useState, useRef, useEffect } from "react";
import { Link, useLocation } from "wouter";
import { Settings, History, HelpCircle, Smartphone, Scissors, Wrench, Shrink, Maximize2, Crop, ChevronDown, FileText, Download, Undo2, ScanLine } from "lucide-react";
import { Button } from "@/components/ui/button";
import { usePwaInstall } from "@/hooks/use-pwa-install";
import logoPath from "@assets/flyerz_logo.png";
import { useBeta } from "@/lib/beta-flag";

interface LayoutProps {
  children: ReactNode;
  currentPhase?: number;
  onStepBack?: (steps: number) => void;
}

const STEP_LABELS: Record<number, string> = {
  1: "Back to Process",
  2: "Back to Review",
  3: "Back to Download",
};

export function Layout({ children, currentPhase, onStepBack }: LayoutProps) {
  const [location] = useLocation();
  const { canInstall, install } = usePwaInstall();
  const betaMode = useBeta();
  const [toolsOpen, setToolsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const toolsRef = useRef<HTMLDivElement>(null);
  const helpRef = useRef<HTMLDivElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (toolsRef.current && !toolsRef.current.contains(e.target as Node)) {
        setToolsOpen(false);
      }
      if (helpRef.current && !helpRef.current.contains(e.target as Node)) {
        setHelpOpen(false);
      }
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setSettingsOpen(false);
      }
    }
    if (toolsOpen || helpOpen || settingsOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [toolsOpen, helpOpen, settingsOpen]);

  const canGoBack = currentPhase && currentPhase > 1 && onStepBack;
  const maxStepsBack = canGoBack ? Math.min(currentPhase! - 1, 3) : 0;

  const isToolsPage = location === "/crop" || location === "/shrink";

  return (
    <div className="min-h-screen bg-background flex flex-col futuristic-bg">
      <div className="orb orb-1" aria-hidden="true" />
      <div className="orb orb-2" aria-hidden="true" />
      <div className="orb orb-3" aria-hidden="true" />
      <header className="sticky top-0 z-50 w-full glow-header">
        <div className="max-w-7xl mx-auto flex h-16 items-center px-4 sm:px-6 lg:px-8 justify-between">
          <div className="flex items-center gap-3" data-testid="header-brand">
            <Link href="/" className="flex items-center gap-2 hover:opacity-90 transition-opacity">
              <img src={logoPath} alt="Flyerz.co.za" className="h-10 sm:h-12 w-auto" data-testid="img-logo" />
            </Link>
          </div>
          
          <nav className="flex items-center gap-1 sm:gap-2">
            {canInstall && (
              <Button
                onClick={install}
                size="sm"
                className="rounded-full font-medium bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20"
                data-testid="button-install-app"
              >
                <Smartphone className="w-4 h-4 mr-1.5" />
                <span className="hidden sm:inline">Install App</span>
                <span className="sm:hidden">Install</span>
              </Button>
            )}
            <Link href="/">
              <Button 
                variant={location === "/" ? "secondary" : "ghost"} 
                size="sm" 
                className="font-medium rounded-full"
                data-testid="nav-dashboard"
              >
                Dashboard
              </Button>
            </Link>

            <div className="relative" ref={toolsRef}>
              <Button 
                variant={isToolsPage || toolsOpen ? "secondary" : "ghost"} 
                size="sm" 
                className="font-medium rounded-full"
                onClick={() => setToolsOpen(!toolsOpen)}
                data-testid="nav-tools"
              >
                <Wrench className="w-4 h-4 mr-1.5" />
                <span className="hidden sm:inline">Manual Tools</span>
                <span className="sm:hidden">Tools</span>
                <ChevronDown className={`w-3 h-3 ml-1 transition-transform ${toolsOpen ? 'rotate-180' : ''}`} />
              </Button>

              {toolsOpen && (
                <div className="absolute right-0 mt-2 w-64 bg-background border border-border/60 rounded-xl shadow-xl shadow-black/10 overflow-hidden z-50" data-testid="tools-dropdown">
                  <div className="p-1.5">
                    <Link href="/crop" onClick={() => setToolsOpen(false)}>
                      <div className="flex items-center gap-3 p-3 rounded-lg cursor-pointer hover:bg-muted/60 transition-colors group" data-testid="tool-link-crop">
                        <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center group-hover:bg-primary/15 transition-colors shrink-0">
                          <Crop className="w-4 h-4 text-primary" />
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-foreground leading-tight">Manual Crop</p>
                          <p className="text-[11px] text-muted-foreground">Draw or type exact crop dimensions</p>
                        </div>
                      </div>
                    </Link>
                    <Link href="/shrink" onClick={() => setToolsOpen(false)}>
                      <div className="flex items-center gap-3 p-3 rounded-lg cursor-pointer hover:bg-muted/60 transition-colors group" data-testid="tool-link-shrink">
                        <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center group-hover:bg-primary/15 transition-colors shrink-0">
                          <Shrink className="w-4 h-4 text-primary" />
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-foreground leading-tight">Safe Margin Shrink</p>
                          <p className="text-[11px] text-muted-foreground">Mirror-extend edges for bleed</p>
                        </div>
                      </div>
                    </Link>
                    <Link href="/?tools=resizer" onClick={() => setToolsOpen(false)}>
                      <div className="flex items-center gap-3 p-3 rounded-lg cursor-pointer hover:bg-muted/60 transition-colors group" data-testid="tool-link-resizer">
                        <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center group-hover:bg-primary/15 transition-colors shrink-0">
                          <Maximize2 className="w-4 h-4 text-primary" />
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-foreground leading-tight">Precision Resizer</p>
                          <p className="text-[11px] text-muted-foreground">Resize to exact mm with CMYK</p>
                        </div>
                      </div>
                    </Link>
                  </div>
                </div>
              )}
            </div>

            <Button variant="ghost" size="sm" className="font-medium rounded-full hidden sm:flex" data-testid="nav-history">
              <History className="w-4 h-4 mr-2" />
              History
            </Button>
            <div className="relative" ref={settingsRef}>
              <Button
                variant="ghost"
                size="icon"
                className="rounded-full"
                data-testid="nav-settings"
                onClick={() => setSettingsOpen(!settingsOpen)}
              >
                <Settings className="w-4 h-4" />
              </Button>
              {settingsOpen && (
                <div className="absolute right-0 top-full mt-2 w-64 rounded-xl border bg-popover/95 backdrop-blur-md shadow-lg p-2 z-50">
                  {canGoBack ? (
                    <>
                      <p className="px-3 pt-2 pb-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Go Back</p>
                      {Array.from({ length: maxStepsBack }, (_, i) => {
                        const stepsBack = i + 1;
                        const targetPhase = currentPhase! - stepsBack;
                        return (
                          <button
                            key={stepsBack}
                            className="flex items-center gap-3 p-3 rounded-lg hover:bg-muted/60 transition-colors group w-full text-left"
                            data-testid={`btn-step-back-${stepsBack}`}
                            onClick={() => {
                              onStepBack!(stepsBack);
                              setSettingsOpen(false);
                            }}
                          >
                            <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center group-hover:bg-primary/15 transition-colors shrink-0">
                              <Undo2 className="w-4 h-4 text-primary" />
                            </div>
                            <div>
                              <p className="text-sm font-semibold text-foreground leading-tight">
                                {stepsBack === 1 ? "Go back 1 step" : `Go back ${stepsBack} steps`}
                              </p>
                              <p className="text-[11px] text-muted-foreground">
                                {STEP_LABELS[targetPhase] || `Phase ${targetPhase}`}
                              </p>
                            </div>
                          </button>
                        );
                      })}
                    </>
                  ) : (
                    <div className="px-3 py-4 text-center">
                      <p className="text-sm text-muted-foreground">No steps to go back to</p>
                      <p className="text-[11px] text-muted-foreground/60 mt-1">Navigate to a job to use step controls</p>
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="relative" ref={helpRef}>
              <Button
                variant="ghost"
                size="icon"
                className="rounded-full"
                data-testid="nav-help"
                onClick={() => setHelpOpen(!helpOpen)}
              >
                <HelpCircle className="w-4 h-4" />
              </Button>
              {helpOpen && (
                <div className="absolute right-0 top-full mt-2 w-72 rounded-xl border bg-popover/95 backdrop-blur-md shadow-lg p-2 z-50">
                  <Link
                    href="/dashboard/rules"
                    className="flex items-center gap-3 p-3 rounded-lg hover:bg-muted/60 transition-colors group"
                    data-testid="link-intelligence-rules"
                    onClick={() => setHelpOpen(false)}
                  >
                    <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center group-hover:bg-primary/15 transition-colors shrink-0">
                      <ScanLine className="w-4 h-4 text-primary" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-foreground leading-tight">Intelligence Rules</p>
                      <p className="text-[11px] text-muted-foreground">System phases + Shrink &amp; Re-Bleed</p>
                    </div>
                  </Link>
                  <a
                    href="/api/checks-guide"
                    download
                    className="flex items-center gap-3 p-3 rounded-lg hover:bg-muted/60 transition-colors group border-t border-border/40 mt-1 pt-3"
                    data-testid="link-checks-guide"
                    onClick={() => setHelpOpen(false)}
                  >
                    <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center group-hover:bg-primary/15 transition-colors shrink-0">
                      <FileText className="w-4 h-4 text-primary" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-foreground leading-tight">System Intelligence Guide (PDF)</p>
                      <p className="text-[11px] text-muted-foreground">Download printable intelligence pack</p>
                    </div>
                    <Download className="w-3.5 h-3.5 text-muted-foreground ml-auto shrink-0" />
                  </a>
                </div>
              )}
            </div>
          </nav>
        </div>
      </header>

      <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 lg:p-8">
        {children}
      </main>
      
      <footer className="py-6 md:px-8 md:py-0 border-t bg-secondary/5 footer-glow">
        <div className="container flex flex-col items-center justify-between gap-4 md:h-16 md:flex-row max-w-7xl mx-auto">
          <p className="text-sm leading-loose text-muted-foreground text-center md:text-left font-medium" data-testid="text-footer-copyright">
            &copy; 2026 Flyerz.co.za Artwork Intelligence. All rights reserved.
          </p>
          <div className="flex items-center gap-3">
            {betaMode && (
              <span
                className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-black uppercase tracking-widest rounded-full bg-yellow-400 text-yellow-950 shadow-sm animate-pulse"
                data-testid="badge-beta-mode"
              >
                &#9889; BETA MODE
              </span>
            )}
            <p className="text-xs text-muted-foreground/60 font-medium">
              Enterprise-grade litho print compliance
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
