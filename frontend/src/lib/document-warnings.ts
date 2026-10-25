// Ready documents can still lack some extracted images (uploads that failed after
// bounded retries). Chat, search and summaries stay available; the UI must not
// present such a document as fully processed.

type TrackedDocument = {
  processing_status: string;
  missing_image_count?: number | null;
};

export const READY_WITH_WARNINGS = "Ready with warnings";

// Positive only for ready documents that recorded missing images. Documents
// processed before tracking existed have null and show no warning.
export function missingImageCount(document: TrackedDocument): number {
  const count = document.missing_image_count;
  return document.processing_status === "ready" && typeof count === "number"
    && Number.isInteger(count) && count > 0
    ? count
    : 0;
}

export function readyLabel(document: TrackedDocument): string {
  return missingImageCount(document) > 0 ? READY_WITH_WARNINGS : "Ready";
}

export function missingImageWarning(document: TrackedDocument): string | null {
  const count = missingImageCount(document);
  if (!count) {
    return null;
  }
  const images = count === 1 ? "1 image" : `${count} images`;
  return `${READY_WITH_WARNINGS}: ${images} could not be processed. `
    + "Some information may be missing from AI responses.";
}
