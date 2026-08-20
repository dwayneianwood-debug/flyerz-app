import { useJobs } from "@/hooks/use-jobs";
import { Card } from "@/components/ui/card";
import { formatDistanceToNow } from "date-fns";
import { FileText, ChevronRight, AlertTriangle } from "lucide-react";
import { Link } from "wouter";
import { StatusBadge } from "./status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { motion } from "framer-motion";

export function JobList() {
  const { data: jobs, isLoading, error } = useJobs();

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
    return null; // Don't show the list if it's empty, keep focus on dropzone
  }

  return (
    <div className="mt-12 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xl font-bold font-display text-foreground">Recent Audits</h3>
      </div>
      
      <div className="grid gap-3">
        {jobs.map((job, idx) => (
          <motion.div
            key={job.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: idx * 0.05 }}
          >
            <Link href={`/job/${job.id}`}>
              <Card className="flex items-center p-4 hover-elevate cursor-pointer border-border/50 group transition-colors hover:border-border">
                <div className="bg-primary/5 p-3 rounded-lg text-primary mr-4 group-hover:bg-primary/10 transition-colors">
                  <FileText className="w-6 h-6" />
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
                  <StatusBadge status={job.status as any} overallPassed={job.auditResults?.overallPassed ?? null} />
                  <ChevronRight className="w-5 h-5 text-muted-foreground group-hover:text-foreground transition-colors" />
                </div>
              </Card>
            </Link>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
