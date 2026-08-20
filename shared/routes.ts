import { z } from 'zod';

// ============================================
// SHARED ERROR SCHEMAS
// ============================================
export const errorSchemas = {
  validation: z.object({
    message: z.string(),
    field: z.string().optional(),
  }),
  notFound: z.object({
    message: z.string(),
  }),
  internal: z.object({
    message: z.string(),
  }),
  fileError: z.object({
    message: z.string(),
    code: z.string().optional(),
  }),
};

// ============================================
// API CONTRACT
// ============================================
export const api = {
  jobs: {
    list: {
      method: 'GET' as const,
      path: '/api/jobs' as const,
      input: z.object({
        status: z.enum(['pending', 'processing', 'complete', 'failed']).optional(),
      }).optional(),
      responses: {
        200: z.array(z.any()), // FileJobResponse[]
      },
    },
    get: {
      method: 'GET' as const,
      path: '/api/jobs/:id' as const,
      responses: {
        200: z.any(), // FileJobResponse
        404: errorSchemas.notFound,
      },
    },
    upload: {
      method: 'POST' as const,
      path: '/api/jobs/upload' as const,
      // Input is FormData with 'file' field, not typed here
      responses: {
        201: z.object({
          jobId: z.number(),
          filename: z.string(),
          status: z.enum(['pending', 'processing', 'complete', 'failed']),
        }),
        400: errorSchemas.validation,
        413: errorSchemas.fileError, // File too large
      },
    },
    process: {
      method: 'POST' as const,
      path: '/api/jobs/:id/process' as const,
      responses: {
        200: z.object({
          message: z.string(),
          jobId: z.number(),
        }),
        404: errorSchemas.notFound,
        500: errorSchemas.internal,
      },
    },
    download: {
      method: 'GET' as const,
      path: '/api/jobs/:id/download/:type' as const,
      // :type is 'original' | 'corrected' | 'report'
      // Response is file stream, not JSON
      responses: {
        200: z.any(), // File stream
        404: errorSchemas.notFound,
      },
    },
    delete: {
      method: 'DELETE' as const,
      path: '/api/jobs/:id' as const,
      responses: {
        204: z.void(),
        404: errorSchemas.notFound,
      },
    },
  },
  manualCrop: {
    preview: {
      method: 'POST' as const,
      path: '/api/manual-crop/preview' as const,
      responses: {
        200: z.object({
          success: z.boolean(),
          pages: z.array(z.any()),
          pageCount: z.number(),
          previewPath: z.string().optional(),
          previewWidth: z.number().optional(),
          previewHeight: z.number().optional(),
          sourceWidth: z.number().optional(),
          sourceHeight: z.number().optional(),
          scale: z.number().optional(),
        }),
        400: errorSchemas.validation,
        500: errorSchemas.internal,
      },
    },
    execute: {
      method: 'POST' as const,
      path: '/api/manual-crop/execute' as const,
      responses: {
        200: z.object({
          success: z.boolean(),
          downloadUrl: z.string().optional(),
          downloadFilename: z.string().optional(),
        }),
        400: errorSchemas.validation,
        500: errorSchemas.internal,
      },
    },
    previewImage: {
      method: 'GET' as const,
      path: '/api/manual-crop/preview-image/:filename' as const,
      responses: {
        200: z.any(),
        404: errorSchemas.notFound,
      },
    },
    download: {
      method: 'GET' as const,
      path: '/api/manual-crop/download/:filename' as const,
      responses: {
        200: z.any(),
        404: errorSchemas.notFound,
      },
    },
  },
  resize: {
    execute: {
      method: 'POST' as const,
      path: '/api/resize' as const,
      responses: {
        200: z.object({
          success: z.boolean(),
          resizedPath: z.string().optional(),
          downloadUrl: z.string().optional(),
          originalWidth: z.number(),
          originalHeight: z.number(),
          targetWidth: z.number(),
          targetHeight: z.number(),
          scalePercent: z.number(),
          method: z.string(),
        }),
        400: errorSchemas.validation,
        500: errorSchemas.internal,
      },
    },
    download: {
      method: 'GET' as const,
      path: '/api/resize/download/:filename' as const,
      responses: {
        200: z.any(),
        404: errorSchemas.notFound,
      },
    },
  },
};

// ============================================
// REQUIRED: buildUrl helper
// ============================================
export function buildUrl(path: string, params?: Record<string, string | number>): string {
  let url = path;
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (url.includes(`:${key}`)) {
        url = url.replace(`:${key}`, String(value));
      }
    });
  }
  return url;
}

// ============================================
// TYPE HELPERS
// ============================================
export type FileUploadResponse = z.infer<typeof api.jobs.upload.responses[201]>;
export type ValidationError = z.infer<typeof errorSchemas.validation>;
export type NotFoundError = z.infer<typeof errorSchemas.notFound>;
export type InternalError = z.infer<typeof errorSchemas.internal>;
export type FileError = z.infer<typeof errorSchemas.fileError>;
