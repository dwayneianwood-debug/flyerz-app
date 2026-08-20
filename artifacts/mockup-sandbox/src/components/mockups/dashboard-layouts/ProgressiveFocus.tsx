import React, { useState } from 'react';
import { 
  Ruler, Upload, Eye, Download, UploadCloud, 
  Settings, History, HelpCircle, Wrench, ChevronDown, 
  File, Crop, Shrink, Maximize2, LayoutDashboard
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import './_group.css';

const sizes = [
  { id: 'a0', name: 'A0', desc: '841 x 1189 mm' },
  { id: 'a1', name: 'A1', desc: '594 x 841 mm' },
  { id: 'a2', name: 'A2', desc: '420 x 594 mm' },
  { id: 'a3', name: 'A3', desc: '297 x 420 mm' },
  { id: 'a4', name: 'A4', desc: '210 x 297 mm' },
  { id: 'a5', name: 'A5', desc: '148 x 210 mm' },
  { id: 'a6', name: 'A6', desc: '105 x 148 mm' },
  { id: 'card', name: 'Business Card', desc: '90 x 50 mm' },
  { id: 'custom', name: 'Custom', desc: 'Enter dimensions' },
];

export default function ProgressiveFocus() {
  const [selectedSize, setSelectedSize] = useState('a4');
  const [isHovering, setIsHovering] = useState(false);

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col font-sans selection:bg-primary/20">
      {/* Ultra-minimal header */}
      <header className="px-6 py-4 flex items-center justify-between z-10">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center text-primary-foreground font-display font-bold text-xl leading-none shadow-md">
            F
          </div>
          <span className="text-xs font-semibold tracking-wider text-muted-foreground uppercase opacity-70">
            Artwork Intelligence
          </span>
        </div>
        
        <nav className="flex items-center gap-2">
          <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-foreground hover:bg-muted/50 rounded-full" title="Dashboard">
            <LayoutDashboard className="w-4 h-4" />
          </Button>
          <div className="relative group">
            <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-foreground hover:bg-muted/50 rounded-full" title="Manual Tools">
              <Wrench className="w-4 h-4" />
            </Button>
          </div>
          <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-foreground hover:bg-muted/50 rounded-full" title="History">
            <History className="w-4 h-4" />
          </Button>
          <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-foreground hover:bg-muted/50 rounded-full" title="Settings">
            <Settings className="w-4 h-4" />
          </Button>
          <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-foreground hover:bg-muted/50 rounded-full" title="Help">
            <HelpCircle className="w-4 h-4" />
          </Button>
        </nav>
      </header>

      {/* Main Content Area - Dropzone Dominates */}
      <main className="flex-1 flex flex-col items-center justify-center p-6 w-full max-w-7xl mx-auto relative">
        
        {/* The Upload Zone */}
        <div 
          className={`w-full max-w-4xl min-h-[50vh] rounded-3xl border-2 border-dashed transition-all duration-300 flex flex-col items-center justify-center text-center p-8 relative overflow-hidden ${
            isHovering 
              ? 'border-primary bg-primary/5 scale-[1.01]' 
              : 'border-border bg-card/50 hover:border-primary/50 hover:bg-muted/30'
          }`}
          onDragOver={(e) => { e.preventDefault(); setIsHovering(true); }}
          onDragLeave={() => setIsHovering(false)}
          onDrop={(e) => { e.preventDefault(); setIsHovering(false); }}
        >
          {/* Subtle decorative background elements */}
          <div className="absolute top-0 left-0 w-full h-full overflow-hidden pointer-events-none opacity-20">
            <div className="absolute -top-[50%] -left-[10%] w-[70%] h-[150%] rounded-full bg-gradient-to-br from-primary/10 to-transparent blur-3xl"></div>
            <div className="absolute -bottom-[50%] -right-[10%] w-[70%] h-[150%] rounded-full bg-gradient-to-tl from-accent/10 to-transparent blur-3xl"></div>
          </div>

          <div className="z-10 flex flex-col items-center max-w-2xl mx-auto py-10">
            {/* Tagline integrated */}
            <h1 className="text-3xl md:text-5xl font-display font-medium tracking-tight mb-4 text-transparent bg-clip-text bg-gradient-to-r from-foreground via-foreground/90 to-foreground/60 animate-in fade-in slide-in-from-bottom-4 duration-700">
              Flawless prints, every single time.
            </h1>
            
            <p className="text-muted-foreground text-sm md:text-base mb-10 max-w-lg">
              Choose your size, drop your file, and we handle the rest. Three steps to print-ready artwork.
            </p>

            <div className={`p-6 rounded-full mb-6 transition-all duration-500 ${isHovering ? 'bg-primary/20 text-primary scale-110' : 'bg-muted text-muted-foreground'}`}>
              <UploadCloud className="w-12 h-12" />
            </div>
            
            <h2 className="text-xl md:text-2xl font-display font-semibold mb-2">
              Drop your artwork here
            </h2>
            
            <div className="flex gap-2 justify-center mb-6 opacity-70">
              <Badge variant="outline" className="bg-background/50 backdrop-blur font-mono text-xs">PDF</Badge>
              <Badge variant="outline" className="bg-background/50 backdrop-blur font-mono text-xs">JPG</Badge>
              <Badge variant="outline" className="bg-background/50 backdrop-blur font-mono text-xs">PNG</Badge>
            </div>
            
            <Button size="lg" className="rounded-full px-8 h-12 text-sm shadow-xl shadow-primary/20 hover:scale-105 transition-transform">
              Browse Files
            </Button>
            
            <p className="text-xs text-muted-foreground/60 mt-4 font-medium">
              Maximum file size 50MB
            </p>
          </div>
        </div>

        {/* Size Selector - Compact Strip Below */}
        <div className="w-full max-w-4xl mt-10">
          <div className="flex items-center gap-3 overflow-x-auto pb-4 no-scrollbar scroll-smooth snap-x">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider shrink-0 mr-2">
              Print Size:
            </span>
            {sizes.map((size) => (
              <button
                key={size.id}
                onClick={() => setSelectedSize(size.id)}
                className={`flex flex-col items-start px-4 py-2 rounded-full border transition-all shrink-0 snap-start ${
                  selectedSize === size.id
                    ? 'border-primary bg-primary text-primary-foreground shadow-md shadow-primary/20'
                    : 'border-border bg-card text-foreground hover:border-primary/40 hover:bg-muted/50'
                }`}
              >
                <span className="text-sm font-semibold">{size.name}</span>
              </button>
            ))}
          </div>
          
          {selectedSize === 'custom' && (
            <div className="flex items-center gap-3 mt-4 animate-in slide-in-from-top-2 fade-in p-3 rounded-2xl bg-muted/30 border border-border/50 max-w-md mx-auto">
              <div className="flex-1 relative">
                <Input type="number" placeholder="Width" className="pl-3 pr-8 h-9 bg-card rounded-xl border-border/50" />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground font-mono">mm</span>
              </div>
              <span className="text-muted-foreground">×</span>
              <div className="flex-1 relative">
                <Input type="number" placeholder="Height" className="pl-3 pr-8 h-9 bg-card rounded-xl border-border/50" />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground font-mono">mm</span>
              </div>
            </div>
          )}
        </div>

      </main>

      {/* 4-Step Indicator - Minimal Bottom Bar */}
      <div className="w-full max-w-md mx-auto mb-8 px-6 mt-auto pt-8">
        <div className="flex items-center justify-between relative">
          <div className="absolute left-0 top-1/2 -translate-y-1/2 w-full h-0.5 bg-border -z-10 rounded-full overflow-hidden">
            <div className="h-full bg-primary w-1/3 rounded-full"></div>
          </div>
          
          {[
            { id: 1, icon: Ruler, label: 'Size', active: true, done: true },
            { id: 2, icon: Upload, label: 'Upload', active: true, done: false },
            { id: 3, icon: Eye, label: 'Review', active: false, done: false },
            { id: 4, icon: Download, label: 'Export', active: false, done: false }
          ].map((step) => (
            <div key={step.id} className="flex flex-col items-center gap-2 bg-background p-1">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center border-2 transition-colors ${
                step.done ? 'bg-primary border-primary text-primary-foreground' :
                step.active ? 'bg-background border-primary text-primary shadow-[0_0_10px_rgba(var(--primary),0.2)]' :
                'bg-background border-border text-muted-foreground'
              }`}>
                <step.icon className="w-4 h-4" />
              </div>
              <span className={`text-[10px] font-semibold uppercase tracking-wider ${
                step.active ? 'text-foreground' : 'text-muted-foreground opacity-50'
              }`}>
                {step.label}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Ultra-minimal Footer */}
      <footer className="py-6 px-6 border-t border-border/30 flex flex-col md:flex-row items-center justify-between gap-4 text-[10px] text-muted-foreground/60 font-medium tracking-wide mt-auto">
        <p>© 2026 Flyerz.co.za Artwork Intelligence</p>
        <div className="flex items-center gap-1.5 opacity-70">
          <div className="w-1.5 h-1.5 rounded-full bg-green-500"></div>
          <span>Enterprise-grade litho print compliance</span>
        </div>
      </footer>
    </div>
  );
}
