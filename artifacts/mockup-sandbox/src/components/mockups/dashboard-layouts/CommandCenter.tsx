import React, { useState } from 'react';
import { 
  Ruler, Upload, Eye, Download, UploadCloud, 
  Settings, History, HelpCircle, Wrench, 
  ChevronDown, File, Crop, Shrink, Maximize2 
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Progress } from '@/components/ui/progress';

import './_group.css';

const paperSizes = [
  { name: 'A0', width: 841, height: 1189 },
  { name: 'A1', width: 594, height: 841 },
  { name: 'A2', width: 420, height: 594 },
  { name: 'A3', width: 297, height: 420 },
  { name: 'A4', width: 210, height: 297 },
  { name: 'A5', width: 148, height: 210 },
  { name: 'A6', width: 105, height: 148 },
  { name: 'Business Card', width: 90, height: 50 },
];

export default function CommandCenter() {
  const [selectedSize, setSelectedSize] = useState('A4');
  const [customWidth, setCustomWidth] = useState('210');
  const [customHeight, setCustomHeight] = useState('297');
  const [isHoveringDropzone, setIsHoveringDropzone] = useState(false);

  const handleSizeSelect = (size: typeof paperSizes[0]) => {
    setSelectedSize(size.name);
    setCustomWidth(size.width.toString());
    setCustomHeight(size.height.toString());
  };

  return (
    <div 
      className="min-h-screen flex flex-col bg-background text-foreground overflow-hidden" 
      style={{ fontFamily: 'var(--font-sans)' }}
    >
      {/* Header - Compact and professional */}
      <header className="flex-none h-14 border-b bg-card px-4 flex items-center justify-between z-10 shadow-sm">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-primary rounded flex items-center justify-center text-primary-foreground font-bold" style={{ fontFamily: 'var(--font-display)' }}>
              F
            </div>
            <div className="flex flex-col">
              <span className="font-bold text-sm leading-none" style={{ fontFamily: 'var(--font-display)' }}>Flyerz.co.za</span>
              <span className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider">Artwork Intelligence</span>
            </div>
          </div>
          
          <Separator orientation="vertical" className="h-6 mx-2 hidden md:block" />
          
          <nav className="hidden md:flex items-center gap-1">
            <Button variant="ghost" size="sm" className="h-8 text-xs font-medium bg-secondary/10">
              Dashboard
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="h-8 text-xs font-medium gap-1">
                  Manual Tools <ChevronDown className="h-3 w-3" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuItem className="text-xs gap-2"><Crop className="h-3 w-3" /> Crop Tool</DropdownMenuItem>
                <DropdownMenuItem className="text-xs gap-2"><Shrink className="h-3 w-3" /> Add Bleed</DropdownMenuItem>
                <DropdownMenuItem className="text-xs gap-2"><Maximize2 className="h-3 w-3" /> Resize</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Button variant="ghost" size="sm" className="h-8 text-xs font-medium gap-1">
              <History className="h-3 w-3" /> History
            </Button>
          </nav>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-foreground">
            <HelpCircle className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-foreground">
            <Settings className="h-4 w-4" />
          </Button>
          <div className="w-8 h-8 rounded-full bg-secondary/20 border border-border flex items-center justify-center text-xs font-medium overflow-hidden">
            <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=Felix" alt="User" />
          </div>
        </div>
      </header>

      {/* Thin Banner for Hero Text */}
      <div className="flex-none bg-secondary text-secondary-foreground px-6 py-2 flex items-center justify-between text-sm shadow-inner">
        <h1 className="font-bold tracking-tight bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent flex-shrink-0 mr-4" style={{ fontFamily: 'var(--font-display)' }}>
          Flawless prints, every single time.
        </h1>
        <p className="text-xs opacity-90 truncate hidden sm:block">
          Choose your size, drop your file, and we handle the rest. Three steps to print-ready artwork.
        </p>
      </div>

      {/* Main Content Area - Grid Layout, No Scrolling */}
      <main className="flex-1 p-4 grid grid-rows-[auto_1fr] gap-4 min-h-0">
        
        {/* Top Row: Wizard Status */}
        <Card className="rounded-md border-border shadow-sm flex-none">
          <CardContent className="p-3">
            <div className="flex items-center justify-between">
              {[
                { step: 1, label: 'Set Size', icon: Ruler, active: true },
                { step: 2, label: 'Upload & Fix', icon: Upload, active: true },
                { step: 3, label: 'Review', icon: Eye, active: false },
                { step: 4, label: 'Download', icon: Download, active: false }
              ].map((item, index, array) => (
                <React.Fragment key={item.step}>
                  <div className={`flex items-center gap-3 ${item.active ? 'opacity-100' : 'opacity-40 grayscale'}`}>
                    <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                      item.active && item.step === 2 ? 'bg-primary text-primary-foreground ring-4 ring-primary/20' : 
                      item.active ? 'bg-primary/20 text-primary' : 
                      'bg-muted text-muted-foreground'
                    }`}>
                      {item.icon ? <item.icon className="w-4 h-4" /> : item.step}
                    </div>
                    <div className="hidden md:block">
                      <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Step {item.step}</div>
                      <div className={`text-sm font-semibold leading-tight ${item.active ? 'text-foreground' : 'text-muted-foreground'}`} style={{ fontFamily: 'var(--font-display)' }}>
                        {item.label}
                      </div>
                    </div>
                  </div>
                  {index < array.length - 1 && (
                    <div className="flex-1 mx-4 max-w-[100px] xl:max-w-[200px]">
                      <Progress value={item.active && array[index + 1]?.active ? 100 : item.active ? 50 : 0} className="h-1" />
                    </div>
                  )}
                </React.Fragment>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Bottom Row: 2-3 Cards arranged in a grid */}
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr_250px] gap-4 min-h-0 h-full">
          
          {/* Card 1: Size Selector */}
          <Card className="rounded-md shadow-sm border-border flex flex-col min-h-0 h-full">
            <CardHeader className="py-3 px-4 border-b flex-none bg-muted/30">
              <CardTitle className="text-base flex items-center gap-2" style={{ fontFamily: 'var(--font-display)' }}>
                <Ruler className="w-4 h-4 text-primary" /> Target Dimensions
              </CardTitle>
              <CardDescription className="text-xs">Select a standard size or enter custom dimensions.</CardDescription>
            </CardHeader>
            <CardContent className="p-4 flex flex-col gap-4 overflow-y-auto min-h-0">
              <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                {paperSizes.map((size) => (
                  <Button
                    key={size.name}
                    variant={selectedSize === size.name ? 'default' : 'outline'}
                    className={`h-auto py-2 px-1 flex flex-col items-center justify-center gap-1 border ${
                      selectedSize === size.name ? 'border-primary ring-1 ring-primary shadow-sm' : 'border-border hover:border-primary/50'
                    }`}
                    onClick={() => handleSizeSelect(size)}
                  >
                    <span className="font-bold text-sm">{size.name}</span>
                    <span className="text-[10px] opacity-70 font-mono">
                      {size.width}×{size.height}
                    </span>
                  </Button>
                ))}
              </div>

              <div className="mt-2">
                <Separator className="mb-4" />
                <Label className="text-xs font-semibold mb-2 block text-muted-foreground uppercase tracking-wider">Custom Size (mm)</Label>
                <div className="flex items-center gap-3">
                  <div className="flex-1 space-y-1">
                    <Label htmlFor="width" className="text-xs">Width</Label>
                    <div className="relative">
                      <Input 
                        id="width" 
                        value={customWidth} 
                        onChange={(e) => {
                          setCustomWidth(e.target.value);
                          setSelectedSize('Custom');
                        }}
                        className="pr-8 h-9 text-sm font-mono"
                      />
                      <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">mm</span>
                    </div>
                  </div>
                  <div className="mt-5 text-muted-foreground">×</div>
                  <div className="flex-1 space-y-1">
                    <Label htmlFor="height" className="text-xs">Height</Label>
                    <div className="relative">
                      <Input 
                        id="height" 
                        value={customHeight} 
                        onChange={(e) => {
                          setCustomHeight(e.target.value);
                          setSelectedSize('Custom');
                        }}
                        className="pr-8 h-9 text-sm font-mono"
                      />
                      <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">mm</span>
                    </div>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Card 2: File Upload Dropzone */}
          <Card className="rounded-md shadow-sm border-border flex flex-col min-h-0 h-full bg-gradient-to-b from-card to-muted/20">
            <CardHeader className="py-3 px-4 border-b flex-none bg-muted/30">
              <CardTitle className="text-base flex items-center gap-2" style={{ fontFamily: 'var(--font-display)' }}>
                <UploadCloud className="w-4 h-4 text-primary" /> Source File
              </CardTitle>
              <CardDescription className="text-xs">Upload your artwork to begin processing.</CardDescription>
            </CardHeader>
            <CardContent className="p-4 flex-1 flex flex-col min-h-0">
              <div 
                className={`flex-1 border-2 border-dashed rounded-lg flex flex-col items-center justify-center p-6 text-center transition-all duration-200 ${
                  isHoveringDropzone ? 'border-primary bg-primary/5 scale-[0.99]' : 'border-border hover:border-primary/50 hover:bg-muted/50'
                }`}
                onMouseEnter={() => setIsHoveringDropzone(true)}
                onMouseLeave={() => setIsHoveringDropzone(false)}
              >
                <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center mb-4 text-primary">
                  <UploadCloud className="w-8 h-8" />
                </div>
                <h3 className="text-lg font-bold mb-1" style={{ fontFamily: 'var(--font-display)' }}>Drop your file here</h3>
                <p className="text-sm text-muted-foreground mb-6 max-w-xs">
                  We'll automatically check bleed, margins, colors, and resolution.
                </p>
                
                <Button className="font-semibold shadow-sm mb-6 px-8">
                  Browse Files
                </Button>

                <div className="flex flex-col items-center gap-2">
                  <div className="flex gap-2">
                    <Badge variant="secondary" className="text-[10px] font-mono rounded-sm px-1.5 py-0">PDF</Badge>
                    <Badge variant="secondary" className="text-[10px] font-mono rounded-sm px-1.5 py-0">JPG</Badge>
                    <Badge variant="secondary" className="text-[10px] font-mono rounded-sm px-1.5 py-0">PNG</Badge>
                  </div>
                  <span className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider">Max file size: 50MB</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Card 3: Quick Stats / Recent */}
          <Card className="rounded-md shadow-sm border-border hidden lg:flex flex-col min-h-0 h-full">
            <CardHeader className="py-3 px-4 border-b flex-none bg-muted/30">
              <CardTitle className="text-base flex items-center gap-2" style={{ fontFamily: 'var(--font-display)' }}>
                <Wrench className="w-4 h-4 text-muted-foreground" /> Operations
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0 flex-1 flex flex-col min-h-0 overflow-y-auto">
              <div className="p-4 space-y-4">
                <div>
                  <h4 className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2">Prepress Checks</h4>
                  <ul className="space-y-2">
                    <li className="flex items-center gap-2 text-xs">
                      <div className="w-1.5 h-1.5 rounded-full bg-primary"></div>
                      <span>Bleed detection & synthesis</span>
                    </li>
                    <li className="flex items-center gap-2 text-xs">
                      <div className="w-1.5 h-1.5 rounded-full bg-primary"></div>
                      <span>Safe margin verification</span>
                    </li>
                    <li className="flex items-center gap-2 text-xs">
                      <div className="w-1.5 h-1.5 rounded-full bg-primary"></div>
                      <span>CMYK color conversion</span>
                    </li>
                    <li className="flex items-center gap-2 text-xs">
                      <div className="w-1.5 h-1.5 rounded-full bg-primary"></div>
                      <span>300 DPI upscaling</span>
                    </li>
                  </ul>
                </div>

                <Separator />

                <div>
                  <h4 className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2">Recent Files</h4>
                  <div className="space-y-2">
                    {[1, 2, 3].map((i) => (
                      <div key={i} className="flex items-center gap-2 p-2 rounded-md hover:bg-muted cursor-pointer transition-colors border border-transparent hover:border-border">
                        <File className="w-4 h-4 text-muted-foreground" />
                        <div className="flex-1 overflow-hidden">
                          <p className="text-xs font-medium truncate">flyer_v{i}_final.pdf</p>
                          <p className="text-[10px] text-muted-foreground">Yesterday • A4</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

        </div>
      </main>

      {/* Minimal Footer */}
      <footer className="flex-none h-8 border-t bg-card px-4 flex items-center justify-between text-[10px] text-muted-foreground">
        <div>© 2026 Flyerz.co.za Artwork Intelligence</div>
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-green-500"></div>
          Enterprise-grade litho print compliance
        </div>
      </footer>
    </div>
  );
}
