import { useState } from "react";
import { useJobs } from "@/hooks/use-jobs";
import { Card } from "@/components/ui/card";
import { formatDistanceToNow } from "date-fns";
import { FileText, ChevronRight, AlertTriangle } from "lucide-react";
import { Link } from "wouter";
import { StatusBadge } from "./status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";

export function JobList() {
  const [attentionOnly, setAttentionOnly] = useState(
    () => new URLSearchParams(window.location.search).get("attention") === "1",
  );
  const { data, isLoading, error, hasNextPage, fetchNextPage, isFetchingNextPage } = useJobs(undefined, attentionOnly);
  const jobs = data?.pages.flatMap((page) => page.jobs) ?? [];

  if (isLoading) {
    return (
      <div className="space-y-4 mt-8">
        <h3 className="text-lg font-bold font-display">Recent Audits</h3>
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-20 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <Card className="p-6 bg-destructive/5 border-destructive/20 text-destructive mt-8">
        <div className="flex items-center gap-3">
          <AlertTriangle className="w-5 h-5" />
          <p className="font-semibold">Failed to load recent jobs.</p>
        </div>
      </Card>
    );
  }

  if (!jobs || jobs.length === 0) {
    if (!attentionOnly) return null;
    return (
      <div className="mt-12 space-y-4" data-testid="job-list-attention">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-xl font-bold font-display text-foreground">Needs a look</h3>
          <Button variant="outline" size="sm" onClick={() => setAttentionOnly(false)} data-testid="button-attention-filter">
            Show all jobs
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">No files need a look.</p>
      </div>
    );
  }

  return (
    <div className="mt-12 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xl font-bold font-display text-foreground">{attentionOnly ? "Needs a look" : "Recent Audits"}</h3>
        <Button
          variant={attentionOnly ? "secondary" : "outline"}
          size="sm"
          onClick={() => setAttentionOnly((value) => !value)}
          data-testid="button-attention-filter"
        >
          {attentionOnly ? "Show all jobs" : "Amber and red only"}
        </Button>
      </div>
      
      <div className="grid gap-3">
        {jobs.map((job, idx) => (
          <motion.div
            key={job.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(idx, 6) * 0.04 }}
          >
            <Link href={`/job/${job.id}`}>
              <Card className="flex items-center p-4 hover-elevate cursor-pointer border-border/50 group transition-colors hover:border-border">
                <div className="bg-primary/5 p-3 rounded-lg text-primary mr-4 group-hover:bg-primary/10 transition-colors overflow-hidden">
                  {job.thumbnailUrl ? (
                    <img src={job.thumbnailUrl} alt="" className="w-6 h-6 object-cover rounded" data-testid={`img-job-thumb-${job.id}`} />
                  ) : (
                    <FileText className="w-6 h-6" />
                  )}
                </div>
                
                <div className="flex-1 min-w-0">
                  <h4 className="font-semibold text-foreground truncate group-hover:text-primary transition-colors">
                    {job.filename}
                  </h4>
                  <div className="flex items-center text-sm text-muted-foreground mt-1 gap-3">
                    <span className="uppercase font-mono text-[10px] bg-muted px-1.5 py-0.5 rounded">
                      {job.fileType}
                    </span>
                    <span>{(job.fileSize / 1024 / 1024).toFixed(2)} MB</span>
                    <span>•</span>
                    <span>{formatDistanceToNow(new Date(job.uploadedAt), { addSuffix: true })}</span>
                  </div>
                </div>

                <div className="flex items-center gap-4 ml-4 shrink-0">
                  {job.quickLight && (
                    <span
                      className={`text-[10px] font-bold uppercase tracking-wide px-2 py-1 rounded-full ${
                        job.quickLight === "green"
                          ? "bg-emerald-100 text-emerald-800"
                          : job.quickLight === "amber"
                            ? "bg-amber-100 text-amber-900"
                            : "bg-red-100 text-red-800"
                      }`}
                      data-testid={`badge-quick-light-${job.id}`}
                    >
                      {job.quickLight}
                    </span>
                  )}
                  <StatusBadge status={job.status as any} overallPassed={job.printReady} />
                  <ChevronRight className="w-5 h-5 text-muted-foreground group-hover:text-foreground transition-colors" />
                </div>
              </Card>
            </Link>
          </motion.div>
        ))}
      </div>
      {hasNextPage && (
        <div className="flex justify-center pt-2">
          <Button
            variant="outline"
            disabled={isFetchingNextPage}
            onClick={() => void fetchNextPage()}
            data-testid="button-load-more-jobs"
          >
            {isFetchingNextPage ? "Loading..." : "Load more"}
          </Button>
        </div>
      )}
    </div>
  );
}
