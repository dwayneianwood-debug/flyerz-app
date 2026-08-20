import React, { useState } from 'react';
import './_group.css';
import { 
  Ruler, Upload, Eye, Download, UploadCloud, Settings, History, 
  HelpCircle, Wrench, ChevronDown, File, Crop, Shrink, Maximize2,
  CheckCircle2, ArrowRight
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';

const SIZES = [
  { id: 'a0', name: 'A0', width: 841, height: 1189 },
  { id: 'a1', name: 'A1', width: 594, height: 841 },
  { id: 'a2', name: 'A2', width: 420, height: 594 },
  { id: 'a3', name: 'A3', width: 297, height: 420 },
  { id: 'a4', name: 'A4', width: 210, height: 297 },
  { id: 'a5', name: 'A5', width: 148, height: 210 },
  { id: 'a6', name: 'A6', width: 105, height: 148 },
  { id: 'bc', name: 'Business Card', width: 90, height: 50 },
];

export default function SplitScreen() {
  const [selectedSize, setSelectedSize] = useState('a4');
  const [customWidth, setCustomWidth] = useState('210');
  const [customHeight, setCustomHeight] = useState('297');
  const [isHovering, setIsHovering] = useState(false);

  const handleSizeSelect = (id: string, width: number, height: number) => {
    setSelectedSize(id);
    setCustomWidth(width.toString());
    setCustomHeight(height.toString());
  };

  return (
    <div className="min-h-screen bg-background font-sans text-foreground flex flex-col">
      {/* Top Navigation */}
      <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center text-primary-foreground font-bold font-display">
                F
              </div>
              <span className="font-display font-semibold text-xl tracking-tight">Flyerz</span>
              <Badge variant="secondary" className="ml-2 font-medium bg-secondary/10 text-secondary hover:bg-secondary/20">
                Artwork Intelligence
              </Badge>
            </div>
            
            <nav className="hidden md:flex items-center gap-1 ml-4 text-sm font-medium">
              <Button variant="ghost" className="text-primary bg-primary/5">Dashboard</Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" className="gap-2 text-muted-foreground hover:text-foreground">
                    <Wrench className="w-4 h-4" />
                    Manual Tools
                    <ChevronDown className="w-3 h-3 opacity-50" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-48">
                  <DropdownMenuItem className="gap-2"><Crop className="w-4 h-4" /> Manual Crop</DropdownMenuItem>
                  <DropdownMenuItem className="gap-2"><Shrink className="w-4 h-4" /> Safe Margin Shrink</DropdownMenuItem>
                  <DropdownMenuItem className="gap-2"><Maximize2 className="w-4 h-4" /> Smart Bleed</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <Button variant="ghost" className="gap-2 text-muted-foreground hover:text-foreground">
                <History className="w-4 h-4" />
                History
              </Button>
            </nav>
          </div>

          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" className="text-muted-foreground">
              <HelpCircle className="w-5 h-5" />
            </Button>
            <Button variant="ghost" size="icon" className="text-muted-foreground">
              <Settings className="w-5 h-5" />
            </Button>
            <div className="w-8 h-8 rounded-full bg-muted border ml-2 flex items-center justify-center text-sm font-medium">
              JD
            </div>
          </div>
        </div>
      </header>

      {/* Main Content Area - Split Screen */}
      <main className="flex-1 flex flex-col md:flex-row">
        
        {/* Left Column - Context & Messaging (approx 35%) */}
        <div className="w-full md:w-[35%] lg:w-[30%] bg-muted/30 border-r border-border p-8 lg:p-12 flex flex-col justify-between">
          <div>
            <div className="mb-12">
              <h1 className="font-display text-4xl lg:text-5xl font-bold leading-tight mb-4 tracking-tight">
                <span className="bg-clip-text text-transparent bg-gradient-to-r from-primary via-blue-600 to-primary/80 animate-gradient bg-[length:200%_auto]">
                  Flawless prints,
                </span>
                <br />
                every single time.
              </h1>
              <p className="text-lg text-muted-foreground leading-relaxed">
                Choose your size, drop your file, and we handle the rest. Three steps to print-ready artwork.
              </p>
            </div>

            {/* Vertical Step Indicator */}
            <div className="space-y-0 relative pl-4">
              {/* Vertical connecting line */}
              <div className="absolute left-[31px] top-6 bottom-8 w-0.5 bg-border rounded-full" />
              <div className="absolute left-[31px] top-6 h-1/3 w-0.5 bg-primary rounded-full z-10" />

              {/* Step 1: Active/Done */}
              <div className="relative z-20 flex items-start gap-4 pb-8">
                <div className="w-10 h-10 rounded-full bg-primary/10 text-primary border-2 border-primary flex items-center justify-center shrink-0 shadow-sm">
                  <Ruler className="w-5 h-5" />
                </div>
                <div className="pt-2">
                  <h3 className="font-semibold text-foreground leading-none mb-1 text-lg">Set Size</h3>
                  <p className="text-sm text-muted-foreground">Define your output dimensions</p>
                </div>
              </div>

              {/* Step 2: Active/Current */}
              <div className="relative z-20 flex items-start gap-4 pb-8">
                <div className="w-10 h-10 rounded-full bg-primary text-primary-foreground flex items-center justify-center shrink-0 shadow-md ring-4 ring-primary/20">
                  <Upload className="w-5 h-5" />
                </div>
                <div className="pt-2">
                  <h3 className="font-semibold text-foreground leading-none mb-1 text-lg">Upload & Fix</h3>
                  <p className="text-sm text-primary font-medium">Drop your file to begin</p>
                </div>
              </div>

              {/* Step 3: Muted */}
              <div className="relative z-20 flex items-start gap-4 pb-8">
                <div className="w-10 h-10 rounded-full bg-muted border-2 border-muted-foreground/20 text-muted-foreground flex items-center justify-center shrink-0">
                  <Eye className="w-5 h-5" />
                </div>
                <div className="pt-2">
                  <h3 className="font-medium text-muted-foreground leading-none mb-1 text-lg">Review</h3>
                  <p className="text-sm text-muted-foreground/70">Check margins and bleed</p>
                </div>
              </div>

              {/* Step 4: Muted */}
              <div className="relative z-20 flex items-start gap-4">
                <div className="w-10 h-10 rounded-full bg-muted border-2 border-muted-foreground/20 text-muted-foreground flex items-center justify-center shrink-0">
                  <Download className="w-5 h-5" />
                </div>
                <div className="pt-2">
                  <h3 className="font-medium text-muted-foreground leading-none mb-1 text-lg">Download</h3>
                  <p className="text-sm text-muted-foreground/70">Get your print-ready PDF</p>
                </div>
              </div>
            </div>
          </div>
          
          <div className="mt-12 p-4 bg-background rounded-xl border border-border/50 shadow-sm">
            <div className="flex items-center gap-3">
              <CheckCircle2 className="w-5 h-5 text-green-500" />
              <div>
                <p className="text-sm font-medium">Enterprise-grade compliance</p>
                <p className="text-xs text-muted-foreground">ISO 12647-2 Litho Standards</p>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column - Action Area (approx 65%) */}
        <div className="w-full md:w-[65%] lg:w-[70%] p-8 lg:p-12 xl:p-16 flex flex-col gap-10 bg-background overflow-y-auto">
          
          {/* Action 1: Size Selection */}
          <section className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="flex items-end justify-between mb-6">
              <div>
                <h2 className="font-display text-2xl font-semibold mb-1">Target Dimensions</h2>
                <p className="text-muted-foreground text-sm">Select a standard size or enter custom dimensions.</p>
              </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
              {/* Presets Grid */}
              <div className="xl:col-span-3">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {SIZES.map((size) => (
                    <button
                      key={size.id}
                      onClick={() => handleSizeSelect(size.id, size.width, size.height)}
                      className={`
                        flex flex-col items-center justify-center p-4 rounded-xl border-2 transition-all
                        ${selectedSize === size.id 
                          ? 'border-primary bg-primary/5 text-primary shadow-sm' 
                          : 'border-border bg-card hover:border-primary/40 hover:bg-muted/50'}
                      `}
                    >
                      <span className="font-semibold text-lg">{size.name}</span>
                      <span className="text-xs opacity-70 mt-1">{size.width} × {size.height} mm</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Custom Size */}
              <div className="xl:col-span-1">
                <Card className={`h-full border-2 ${selectedSize === 'custom' ? 'border-primary bg-primary/5' : 'border-border'}`}>
                  <CardContent className="p-4 flex flex-col justify-center h-full gap-3">
                    <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1 block">Custom Size (mm)</Label>
                    <div className="flex items-center gap-2">
                      <div className="space-y-1.5 flex-1">
                        <Input 
                          id="width" 
                          value={customWidth} 
                          onChange={(e) => { setCustomWidth(e.target.value); setSelectedSize('custom'); }}
                          className="h-9 text-center font-medium bg-background" 
                        />
                      </div>
                      <span className="text-muted-foreground font-medium">×</span>
                      <div className="space-y-1.5 flex-1">
                        <Input 
                          id="height" 
                          value={customHeight} 
                          onChange={(e) => { setCustomHeight(e.target.value); setSelectedSize('custom'); }}
                          className="h-9 text-center font-medium bg-background" 
                        />
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </div>
          </section>

          <Separator className="my-2" />

          {/* Action 2: File Upload */}
          <section className="animate-in fade-in slide-in-from-bottom-4 duration-500 delay-100 flex-1 flex flex-col">
            <div className="mb-6">
              <h2 className="font-display text-2xl font-semibold mb-1">Upload Artwork</h2>
              <p className="text-muted-foreground text-sm">We'll automatically add bleed, fix margins, and convert colors.</p>
            </div>

            <div 
              className={`
                flex-1 min-h-[300px] border-3 border-dashed rounded-2xl transition-all duration-200 ease-in-out
                flex flex-col items-center justify-center p-12 text-center
                ${isHovering 
                  ? 'border-primary bg-primary/5 scale-[1.01] shadow-lg' 
                  : 'border-border bg-muted/20 hover:bg-muted/40 hover:border-primary/50'}
              `}
              onDragOver={(e) => { e.preventDefault(); setIsHovering(true); }}
              onDragLeave={() => setIsHovering(false)}
              onDrop={(e) => { e.preventDefault(); setIsHovering(false); }}
            >
              <div className={`p-6 rounded-full bg-background shadow-sm mb-6 transition-transform duration-300 ${isHovering ? 'scale-110 shadow-md text-primary' : 'text-muted-foreground'}`}>
                <UploadCloud className="w-12 h-12" />
              </div>
              
              <h3 className="font-display text-2xl font-semibold mb-2">Drop your file here</h3>
              <p className="text-muted-foreground mb-8 max-w-sm">
                or click to browse from your computer
              </p>
              
              <Button size="lg" className="h-12 px-8 text-base shadow-sm group">
                Browse Files
                <ArrowRight className="w-4 h-4 ml-2 group-hover:translate-x-1 transition-transform" />
              </Button>
              
              <div className="flex items-center gap-3 mt-8">
                <Badge variant="outline" className="text-xs py-1 px-3 bg-background font-mono font-medium text-muted-foreground">PDF</Badge>
                <Badge variant="outline" className="text-xs py-1 px-3 bg-background font-mono font-medium text-muted-foreground">JPG</Badge>
                <Badge variant="outline" className="text-xs py-1 px-3 bg-background font-mono font-medium text-muted-foreground">PNG</Badge>
                <span className="text-xs text-muted-foreground ml-2 flex items-center"><File className="w-3 h-3 mr-1 inline" /> Max 50MB</span>
              </div>
            </div>
          </section>
        </div>
      </main>

      {/* Footer */}
      <footer className="w-full border-t bg-background py-6 px-4 md:px-8 mt-auto z-10">
        <div className="container mx-auto flex flex-col md:flex-row items-center justify-between text-sm text-muted-foreground">
          <p>© 2026 Flyerz.co.za Artwork Intelligence</p>
          <div className="flex items-center gap-6 mt-4 md:mt-0">
            <span className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-green-500"></span>
              Enterprise-grade litho print compliance
            </span>
            <div className="flex gap-4">
              <a href="#" className="hover:text-foreground transition-colors">Terms</a>
              <a href="#" className="hover:text-foreground transition-colors">Privacy</a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
