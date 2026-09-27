import { useInfiniteQuery, useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, buildUrl } from "@shared/routes";
import type { FileJobResponse, FileUploadResponse, BleedOptions, JobListPage } from "@shared/schema";
import { extractSafeZoneLayoutMessage } from "@/lib/safe-zone-error";
import { jobPollIntervalMs } from "@/lib/poll-backoff";

function errorMessageFromResponseBody(body: unknown, fallback: string): string {
  if (body == null) return fallback;
  if (typeof body === "string") {
    const t = body.trim();
    if (!t) return fallback;
    try {
      const parsed = JSON.parse(t);
      return errorMessageFromResponseBody(parsed, t);
    } catch {
      return t;
    }
  }
  if (typeof body === "object") {
    const o = body as Record<string, unknown>;
    const msg =
      (typeof o.message === "string" && o.message.trim()) ||
      (typeof o.error === "string" && o.error.trim()) ||
      "";
    if (msg) return msg;
  }
  return fallback;
}

// Custom error handling for fetch
async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let errorMsg = res.statusText || "An error occurred";
    let errorBody: unknown = null;
    try {
      errorBody = await res.json();
      errorMsg = errorMessageFromResponseBody(errorBody, errorMsg);
    } catch {
      try {
        const text = await res.text();
        errorMsg = errorMessageFromResponseBody(text, errorMsg);
        errorBody = text;
      } catch {
        /* keep statusText */
      }
    }
    const err = new Error(errorMsg) as Error & { status?: number; response?: { data?: unknown } };
    err.status = res.status;
    err.response = { data: errorBody };
    const layoutMsg = extractSafeZoneLayoutMessage(err);
    if (layoutMsg) err.message = layoutMsg;
    throw err;
  }
  return res.json();
}

export function useJobs(statusFilter?: "pending" | "processing" | "complete" | "failed") {
  return useInfiniteQuery({
    queryKey: ["jobs", statusFilter],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const url = new URL(api.jobs.list.path, window.location.origin);
      url.searchParams.set("limit", "50");
      url.searchParams.set("offset", String(pageParam));
      if (statusFilter) {
        url.searchParams.append("status", statusFilter);
      }
      const res = await fetch(url.toString(), { credentials: "include" });
      return handleResponse<JobListPage>(res);
    },
    getNextPageParam: (last) => (last.hasMore ? last.offset + last.jobs.length : undefined),
  });
}

export function useJob(id: number) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: async () => {
      const url = buildUrl(api.jobs.get.path, { id });
      const res = await fetch(url, { credentials: "include" });
      return handleResponse<FileJobResponse>(res);
    },
    refetchInterval: (query) => jobPollIntervalMs(query.state?.data?.status, query.state.dataUpdateCount),
  });
}

export function useUploadJob() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: async ({ file, fileName, bleedOptions }: { file: File | Blob; fileName?: string; bleedOptions?: BleedOptions }) => {
      const formData = new FormData();
      const name = fileName || (file instanceof File ? file.name : 'upload');
      formData.append("file", file, name);
      if (bleedOptions) {
        formData.append("bleedOptions", JSON.stringify(bleedOptions));
        const tw = bleedOptions.targetWidth;
        const th = bleedOptions.targetHeight;
        if (tw != null && th != null && Number(tw) > 0 && Number(th) > 0) {
          formData.append("targetWidthMm", String(tw));
          formData.append("targetHeightMm", String(th));
        }
      }

      const res = await fetch(api.jobs.upload.path, {
        method: api.jobs.upload.method,
        body: formData,
        credentials: "include",
      });
      
      return handleResponse<FileUploadResponse>(res);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useProcessJob() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: async ({ id, bleedOptions }: { id: number; bleedOptions?: Record<string, any> }) => {
      const url = buildUrl(api.jobs.process.path, { id });
      const body: Record<string, any> = {};
      if (bleedOptions) {
        const { cropX, cropY, cropWidth, cropHeight, ...rest } = bleedOptions;
        if (Object.keys(rest).length > 0) body.bleedOptions = rest;
        if (cropX !== undefined) body.cropX = cropX;
        if (cropY !== undefined) body.cropY = cropY;
        if (cropWidth !== undefined) body.cropWidth = cropWidth;
        if (cropHeight !== undefined) body.cropHeight = cropHeight;
        const tw = bleedOptions.targetWidth;
        const th = bleedOptions.targetHeight;
        if (tw != null && th != null && Number(tw) > 0 && Number(th) > 0) {
          body.targetWidthMm = String(tw);
          body.targetHeightMm = String(th);
        }
      }
      const res = await fetch(url, {
        method: api.jobs.process.method,
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      return handleResponse<{ message: string; jobId: number }>(res);
    },
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["job", id] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["job", id] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useDeleteJob() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: async (id: number) => {
      const url = buildUrl(api.jobs.delete.path, { id });
      const res = await fetch(url, {
        method: api.jobs.delete.method,
        credentials: "include",
      });
      if (!res.ok) {
        throw new Error("Failed to delete job");
      }
      return true;
    },
    onSuccess: (_, id) => {
      // Remove from list
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      // Remove specific cache
      queryClient.removeQueries({ queryKey: ["job", id] });
    },
  });
}
