import { Badge } from "@/components/ui/badge";
import { Loader2, CheckCircle2, XCircle, Clock, AlertCircle } from "lucide-react";
import { JobStatus } from "@shared/schema";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface StatusBadgeProps {
  status: JobStatus;
  className?: string;
  overallPassed?: boolean | null;
}

export function StatusBadge({ status, className, overallPassed }: StatusBadgeProps) {
  switch (status) {
    case "pending":
      return (
        <Badge variant="outline" className={cn("bg-muted text-muted-foreground font-medium py-1 px-3 border-muted-foreground/20", className)}>
          <Clock className="w-3.5 h-3.5 mr-1.5" />
          Pending
        </Badge>
      );
    case "queued":
      return (
        <Badge variant="outline" className={cn("bg-amber-500/15 text-amber-700 font-medium py-1 px-3 border-amber-500/20", className)} data-testid="badge-queued">
          <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
          Queued
        </Badge>
      );
    case "processing":
      return (
        <Badge variant="secondary" className={cn("bg-primary/10 text-primary hover:bg-primary/20 font-medium py-1 px-3 border-transparent shadow-none", className)}>
          <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
          Processing
        </Badge>
      );
    case "complete":
      if (overallPassed === true) {
        return (
          <Badge variant="default" className={cn("bg-green-500/15 text-green-700 hover:bg-green-500/25 border-transparent shadow-none font-medium py-1 px-3", className)} data-testid="badge-print-ready">
            <CheckCircle2 className="w-3.5 h-3.5 mr-1.5" />
            Print Ready
          </Badge>
        );
      }
      return (
        <Badge variant="default" className={cn("bg-amber-500/15 text-amber-700 hover:bg-amber-500/25 border-transparent shadow-none font-medium py-1 px-3", className)} data-testid="badge-review-required">
          <AlertCircle className="w-3.5 h-3.5 mr-1.5" />
          Review Required
        </Badge>
      );
    case "failed":
      return (
        <Badge variant="destructive" className={cn("bg-destructive/10 text-destructive hover:bg-destructive/20 border-transparent shadow-none font-medium py-1 px-3", className)}>
          <XCircle className="w-3.5 h-3.5 mr-1.5" />
          Issues Found
        </Badge>
      );
  }
}
