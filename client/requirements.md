## Packages
react-dropzone | Drag and drop file upload handling
framer-motion | Smooth page transitions and status animations
date-fns | Formatting upload and completion timestamps
clsx | Class merging (ensure available)
tailwind-merge | Class merging (ensure available)

## Notes
- File upload uses multipart/form-data to POST /api/jobs/upload
- Downloads use standard <a> tags pointing to /api/jobs/:id/download/:type to trigger browser download
- Polling is implemented via TanStack Query refetchInterval on the job details page while status is pending/processing
- Assuming @shared/routes and @shared/schema exist with the exact types provided in the prompt
