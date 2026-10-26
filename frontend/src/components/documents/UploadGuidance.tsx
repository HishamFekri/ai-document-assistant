import { compactUploadGuidance, uploadGuidance, type UploadPolicy } from "@/lib/upload-policy";

export default function UploadGuidance({ policy, error, className }: {
  policy: UploadPolicy | null;
  error?: boolean;
  className?: string;
}) {
  return (
    <p className={className ?? "mt-2 text-xs leading-5 text-[var(--text-muted)]"} aria-live="polite">
      {policy ? (
        <>
          <span className="text-[11px] tracking-[-0.005em] sm:hidden">{compactUploadGuidance(policy)}</span>
          <span className="hidden sm:inline">{uploadGuidance(policy)}</span>
        </>
      ) : error
        ? "Upload limits are unavailable. Refresh the page to try again."
        : "Loading upload limits…"}
    </p>
  );
}
