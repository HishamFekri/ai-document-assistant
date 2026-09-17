export type UploadPolicy = {
  supported_extensions: string[];
  max_file_bytes: number;
  max_pdf_pages: number;
};

export function parseUploadPolicy(value: unknown): UploadPolicy {
  const policy = value as Partial<UploadPolicy> | null;
  if (!policy || !Array.isArray(policy.supported_extensions)
    || policy.supported_extensions.length === 0
    || !policy.supported_extensions.every((extension) =>
      typeof extension === "string" && /^\.[a-z0-9]+$/.test(extension))
    || !Number.isSafeInteger(policy.max_file_bytes) || Number(policy.max_file_bytes) <= 0
    || !Number.isSafeInteger(policy.max_pdf_pages) || Number(policy.max_pdf_pages) <= 0) {
    throw new Error("Upload limits are unavailable. Please try again shortly.");
  }
  return policy as UploadPolicy;
}

export function uploadGuidance(policy: UploadPolicy): string {
  const types = policy.supported_extensions.map((extension) => extension.slice(1).toUpperCase()).join(", ");
  return `Supported: ${types}. Maximum file size: ${policy.max_file_bytes / 1024 ** 2} MB. PDFs: up to ${policy.max_pdf_pages} pages.`;
}

export function validateUpload(file: Pick<File, "name" | "size">, policy: UploadPolicy): string | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!policy.supported_extensions.includes(extension)) {
    return `Unsupported file type. Allowed: ${policy.supported_extensions.map((item) => item.slice(1).toUpperCase()).join(", ")}.`;
  }
  if (file.size === 0) return "Uploaded file is empty.";
  if (file.size > policy.max_file_bytes) {
    return `This file is too large. The maximum allowed size is ${policy.max_file_bytes / 1024 ** 2} MB.`;
  }
  return null;
}

// Upload failures use fixed public messages. Never render arbitrary proxy HTML,
// validation objects, or parser exceptions, even when a response supplies a code.
export async function uploadError(response: Response): Promise<string> {
  const fallback = "Could not upload document. Please try again.";
  let data: { code?: string; detail?: unknown } = {};
  try { data = await response.json() ?? {}; } catch { /* An edge may return HTML. */ }
  if (data.code === "file_size" && typeof data.detail === "string"
    && /^This file is too large\. The maximum allowed size is \d+ MB\.$/.test(data.detail)) return data.detail;
  if (data.code === "pdf_pages" && typeof data.detail === "string"
    && /^This PDF has too many pages\. The maximum allowed is \d+ pages\.$/.test(data.detail)) return data.detail;
  const messages: Record<string, string> = {
    empty_file: "Uploaded file is empty.",
    unsupported_file: "Unsupported file type. Choose one of the displayed supported types.",
    invalid_pdf: "This PDF could not be read. Please upload a valid, unencrypted PDF.",
    advanced_pages: "This PDF needs advanced extraction for too many pages. Split it into smaller files.",
    office_expansion: "This compressed document expands beyond the processing limits. Please use a smaller file.",
    invalid_office: "This Office document could not be read safely. Please export it again and retry.",
    spreadsheet_limit: "This spreadsheet is too large to process safely. Reduce its sheets, rows or columns.",
    docx_limit: "This Word document is too large to process safely. Split it into smaller files.",
    content_limit: "This document contains too much extractable content. Split it into smaller files.",
    invalid_text: "TXT files must contain valid UTF-8 text without null characters.",
    upload_form: "Upload exactly one file without additional form fields.",
    processing_quota: "Please wait for your current document processing to finish.",
    document_quota: "Your document limit has been reached. Delete a document before uploading another.",
    storage_quota: "Your document storage limit has been reached. Delete documents before uploading more.",
    concurrency_limit: "An upload is already being admitted. Please wait and try again.",
  };
  if (data.code && Object.hasOwn(messages, data.code)) return messages[data.code];
  if (response.status === 413) return "This upload is too large. Use a smaller file within the displayed limits.";
  if (response.status === 429) return "The upload limit has been reached. Please wait and try again.";
  if (response.status === 401) return "Your session has expired. Please sign in again.";
  if (response.status === 503) return "Uploads are temporarily unavailable. Please try again shortly.";
  return fallback;
}
