"use client";

import {
  type CSSProperties,
  useEffect,
  useState,
} from "react";

import {
  File,
  FileSpreadsheet,
  FileText,
  FileType2,
  Loader2,
  Trash2,
  X,
} from "lucide-react";

import {
  Document,
} from "@/types/chat";

import {
  missingImageWarning,
  readyLabel,
} from "@/lib/document-warnings";


// Matches the closing animation in globals.css (190ms) with a little slack.
const CLOSE_DURATION_MS = 220;


type Props = {
  open: boolean;

  documents: Document[];

  activeDocumentId?: number | null;

  onClose: () => void;

  onRemove: (
    documentId: number
  ) => void;

  onOpenSummary: (
    document: Document
  ) => void;
};


function getFileExtension(
  filename: string
) {
  const parts =
    filename
      .toLowerCase()
      .split(".");

  if (parts.length < 2) {
    return "";
  }

  return parts[
    parts.length - 1
  ];
}


function FileIcon({
  filename,
}: {
  filename: string;
}) {
  const extension =
    getFileExtension(
      filename
    );

  const Icon =
    extension === "pdf"
    || extension === "txt"
      ? FileText
      : extension === "doc"
        || extension === "docx"
        ? FileType2
        : extension === "xls"
          || extension === "xlsx"
          ? FileSpreadsheet
          : File;

  return (
    <Icon
      size={16}
      strokeWidth={1.7}
      aria-hidden="true"
      className="
        mt-0.5
        shrink-0
        text-[var(--text-muted)]
        transition-colors
        duration-200
        group-hover:text-[var(--text-secondary)]
      "
    />
  );
}


function getFileLabel(
  filename: string
) {
  const extension =
    getFileExtension(
      filename
    );

  if (extension === "pdf") {
    return "PDF";
  }

  if (
    extension === "doc"
    || extension === "docx"
  ) {
    return "Word";
  }

  if (
    extension === "xls"
    || extension === "xlsx"
  ) {
    return "Excel";
  }

  if (extension === "txt") {
    return "Text";
  }

  return (
    extension
      ? extension.toUpperCase()
      : "File"
  );
}


function DocumentStatus({
  document,
  warning,
}: {
  document: Document;
  warning: string | null;
}) {
  if (
    document.processing_status
    === "processing"
  ) {
    return (
      <span
        className="
          inline-flex
          items-center
          gap-1
        "
      >
        <Loader2
          size={11}
          aria-hidden="true"
          className="
            animate-spin
          "
        />

        {document.processing_progress
          ?? 0}
        %
      </span>
    );
  }

  const failed =
    document.processing_status
    === "failed";

  return (
    <span
      className="
        inline-flex
        items-center
        gap-1.5
      "
    >
      <span
        aria-hidden="true"
        className={`
          h-1.5
          w-1.5
          rounded-full
          ${
            failed
              ? "bg-[var(--danger)]"
              : warning
                ? "bg-[var(--warning)]"
                : "bg-[var(--ok)]"
          }
        `}
      />

      <span
        className={
          failed
            ? "text-[var(--danger)]"
            : undefined
        }
      >
        {failed
          ? "Failed"
          : warning
            // The warning line below spells out "Ready with warnings: …".
            ? "Ready"
            : readyLabel(
                document
              )}
      </span>
    </span>
  );
}


export default function UploadedDocsPanel({
  open,
  documents,
  activeDocumentId = null,
  onClose,
  onRemove,
  onOpenSummary,
}: Props) {
  // Stay mounted while the closing animation plays.
  const [
    rendered,
    setRendered,
  ] = useState(open);

  if (
    open
    && !rendered
  ) {
    setRendered(true);
  }

  // Unmount on a timer rather than animationend, which never fires when the
  // animation is skipped (background tab, reduced motion).
  useEffect(() => {
    if (
      open
      || !rendered
    ) {
      return;
    }

    const timeoutId =
      window.setTimeout(
        () => setRendered(false),
        CLOSE_DURATION_MS
      );

    return () => window.clearTimeout(timeoutId);
  }, [
    open,
    rendered,
  ]);

  if (!rendered) {
    return null;
  }


  return (
    <aside
      aria-label="Files in this chat"
      aria-hidden={!open}
      inert={!open}
      data-state={
        open
          ? "open"
          : "closed"
      }
      className="
        aura-files
        absolute
        bottom-2
        right-2
        top-2
        z-40
        flex
        w-[min(300px,calc(100%-1rem))]
        flex-col
        overflow-hidden
        rounded-[18px]
        border
        border-[var(--border-soft)]
        bg-[var(--sidebar)]
        text-[var(--text-primary)]
        shadow-[0_24px_60px_-24px_rgba(0,0,0,0.55)]
        xl:relative
        xl:inset-auto
        xl:my-2
        xl:mr-2
        xl:w-[300px]
        xl:shrink-0
        xl:shadow-none
      "
    >
      <div
        className="
          flex
          h-14
          shrink-0
          items-center
          justify-between
          gap-2
          pl-4
          pr-2
        "
      >
        <div
          className="
            flex
            items-baseline
            gap-2
          "
        >
          <h2
            className="
              text-[14px]
              font-semibold
              tracking-[-0.01em]
            "
          >
            Files
          </h2>

          <span
            className="
              text-[12px]
              tabular-nums
              text-[var(--text-muted)]
            "
          >
            {documents.length}
          </span>
        </div>


        <button
          type="button"
          onClick={
            onClose
          }
          title="Close files"
          aria-label="Close files"
          className="
            flex
            h-8
            w-8
            items-center
            justify-center
            rounded-full
            text-[var(--text-muted)]
            transition
            duration-200
            hover:bg-[var(--surface-hover)]
            hover:text-[var(--text-primary)]
          "
        >
          <X
            size={16}
          />
        </button>
      </div>


      <div
        className="
          min-h-0
          flex-1
          overflow-y-auto
          px-2
          pb-3
        "
      >
        {documents.length === 0 ? (
          <div
            className="
              flex
              h-full
              flex-col
              items-center
              justify-center
              px-6
              pb-10
              text-center
            "
          >
            <p
              className="
                text-[13px]
                font-medium
                text-[var(--text-primary)]
              "
            >
              No documents yet
            </p>

            <p
              className="
                mt-1
                text-[12px]
                leading-5
                text-[var(--text-muted)]
              "
            >
              Attach files using the
              + button in the chat.
            </p>
          </div>
        ) : (
          <ul
            className="
              space-y-0.5
            "
          >
            {documents.map(
              (
                document,
                index
              ) => {
                const ready =
                  document.processing_status
                  !== "processing"
                  && document.processing_status
                  !== "failed";

                const active =
                  document.id
                  === activeDocumentId;

                const warning =
                  missingImageWarning(
                    document
                  );

                const pages =
                  document.pages_count
                    ? `${document.pages_count} ${
                        document.pages_count === 1
                          ? "page"
                          : "pages"
                      }`
                    : null;


                return (
                  <li
                    key={
                      document.id
                    }
                    style={{
                      "--i": index,
                    } as CSSProperties}
                    className="aura-file-row"
                  >
                    <div
                      role={
                        ready
                          ? "button"
                          : undefined
                      }
                      tabIndex={
                        ready
                          ? 0
                          : -1
                      }
                      aria-current={
                        active
                          ? "true"
                          : undefined
                      }
                      onClick={() => {
                        if (!ready) {
                          return;
                        }

                        onOpenSummary(
                          document
                        );
                      }}
                      onKeyDown={(
                        event
                      ) => {
                        if (!ready) {
                          return;
                        }

                        if (
                          event.key === "Enter"
                          || event.key === " "
                        ) {
                          event.preventDefault();

                          onOpenSummary(
                            document
                          );
                        }
                      }}
                      className={`
                        group
                        relative
                        flex
                        items-start
                        gap-2.5
                        rounded-xl
                        py-2.5
                        pl-3
                        pr-1.5
                        outline-none
                        transition-colors
                        duration-200
                        focus-visible:ring-2
                        focus-visible:ring-[var(--focus-ring)]

                        ${
                          active
                            ? "bg-[var(--surface-active)]"
                            : ready
                              ? "cursor-pointer hover:bg-[var(--surface-hover)]"
                              : ""
                        }
                      `}
                    >
                      <FileIcon
                        filename={
                          document.filename
                        }
                      />


                      <div
                        className="
                          min-w-0
                          flex-1
                        "
                      >
                        <p
                          title={
                            document.filename
                          }
                          className="
                            truncate
                            text-[13px]
                            font-medium
                            leading-5
                            text-[var(--text-primary)]
                          "
                        >
                          {document.filename}
                        </p>


                        <p
                          className="
                            mt-0.5
                            flex
                            min-w-0
                            items-center
                            gap-1.5
                            whitespace-nowrap
                            text-[11.5px]
                            leading-4
                            text-[var(--text-muted)]
                          "
                        >
                          <span>
                            {getFileLabel(
                              document.filename
                            )}
                          </span>

                          {pages && (
                            <>
                              <span aria-hidden="true">
                                ·
                              </span>

                              <span className="tabular-nums">
                                {pages}
                              </span>
                            </>
                          )}

                          <span aria-hidden="true">
                            ·
                          </span>

                          <DocumentStatus
                            document={
                              document
                            }
                            warning={
                              warning
                            }
                          />
                        </p>


                        {document.processing_status
                          === "processing" && (
                          <span
                            aria-hidden="true"
                            className="
                              mt-2
                              block
                              h-0.5
                              overflow-hidden
                              rounded-full
                              bg-[var(--surface-active)]
                            "
                          >
                            <span
                              className="
                                block
                                h-full
                                rounded-full
                                bg-[var(--text-secondary)]
                                transition-[width]
                                duration-500
                              "
                              style={{
                                width: `${Math.min(
                                  100,
                                  Math.max(
                                    4,
                                    document.processing_progress
                                    ?? 0
                                  )
                                )}%`,
                              }}
                            />
                          </span>
                        )}


                        {warning && (
                          <p
                            className="
                              mt-1.5
                              text-[11.5px]
                              leading-[1.45]
                              text-[var(--warning)]
                            "
                          >
                            {warning}
                          </p>
                        )}
                      </div>


                      <button
                        type="button"
                        onClick={(
                          event
                        ) => {
                          event.stopPropagation();

                          onRemove(
                            document.id
                          );
                        }}
                        title="Remove file"
                        aria-label={`Remove ${document.filename}`}
                        className="
                          flex
                          h-7
                          w-7
                          shrink-0
                          items-center
                          justify-center
                          rounded-lg
                          text-[var(--text-muted)]
                          transition
                          duration-200
                          hover:bg-[var(--danger)]/10
                          hover:text-[var(--danger)]
                          focus-visible:opacity-100
                          md:opacity-0
                          md:group-hover:opacity-100
                          md:group-focus-within:opacity-100
                        "
                      >
                        <Trash2
                          size={14}
                        />
                      </button>
                    </div>
                  </li>
                );
              }
            )}
          </ul>
        )}
      </div>
    </aside>
  );
}
