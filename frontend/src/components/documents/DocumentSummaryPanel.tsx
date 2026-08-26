"use client";

import {
  AlertTriangle,
  ChevronDown,
  Loader2,
  MoreHorizontal,
  Trash2,
} from "lucide-react";

import {
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  ChangeEvent,
  RefObject,
} from "react";

import SummaryBlock from "@/components/documents/SummaryBlock";
import SummaryAssistant from "@/components/documents/SummaryAssistant";

import {
  useDocumentSummary,
} from "@/hooks/useDocumentSummary";

import type {
  SummaryMode,
} from "@/lib/summary-api";

import type {
  DocumentSummary,
} from "@/types/summary";


type SummaryDocument = {
  id: number;

  filename: string;

  file_type?: string | null;

  pages_count?: number | null;

  processing_status?: string | null;
};


type Props = {
  open: boolean;

  chatId:
    number | null;

  documentId:
    number | null;

  filename:
    string | null;

  pagesCount?:
    number | null;

  token:
    string | null;

  mode?: SummaryMode;

  uploading?: boolean;

  fileInputRef?:
    RefObject<HTMLInputElement | null>;

  onUpload?: (
    event:
      ChangeEvent<HTMLInputElement>
  ) => void;

  documents:
    SummaryDocument[];

  onSelectDocument?: (
    document: SummaryDocument
  ) => void;
};


export default function DocumentSummaryPanel({
  open,
  chatId,
  documentId,
  filename,
  pagesCount,
  token,
  mode = "summary",
  uploading = false,
  fileInputRef,
  onUpload,
  documents,
  onSelectDocument,
}: Props) {
  const [
    menuOpen,
    setMenuOpen,
  ] = useState(false);

  const [
    documentsOpen,
    setDocumentsOpen,
  ] = useState(false);

  const [
    deleteConfirmOpen,
    setDeleteConfirmOpen,
  ] = useState(false);

  const [
    deleteError,
    setDeleteError,
  ] = useState<
    string | null
  >(null);


  const [
    stoppedSnapshot,
    setStoppedSnapshot,
  ] = useState<
    DocumentSummary | null
  >(null);


  const menuRef =
    useRef<HTMLDivElement | null>(
      null
    );

  const documentsRef =
    useRef<HTMLDivElement | null>(
      null
    );


  const {
    selectedSummary,

    loading,
    generating,
    deletingSummaryId,

    error,

    regenerateSummary,
    stopGeneration,

    addGeneratedSummary,
    removeSummary,

    clearError,
  } = useDocumentSummary({
    token,
    chatId,
    documentId,
    mode,
  });


  const currentDocument =
    documents.find(
      (document) =>
        document.id
        === documentId
    )
    ?? null;


  const currentFilename =
    currentDocument?.filename
    ?? filename
    ?? "Document";


  const documentReady =
    !currentDocument
    || !currentDocument
      .processing_status
    || currentDocument
      .processing_status
      === "ready";


  const modeLabel =
    mode === "transcription"
      ? "Transcription"
      : "Summary";

  const modeLabelLower =
    mode === "transcription"
      ? "transcription"
      : "summary";


  useEffect(() => {
    function handleOutsideClick(
      event: MouseEvent
    ) {
      const target =
        event.target as Node;

      if (
        menuRef.current
        && !menuRef.current.contains(
          target
        )
      ) {
        setMenuOpen(false);
      }

      if (
        documentsRef.current
        && !documentsRef.current.contains(
          target
        )
      ) {
        setDocumentsOpen(false);
      }
    }


    function handleEscape(
      event: KeyboardEvent
    ) {
      if (
        event.key
        === "Escape"
      ) {
        setMenuOpen(false);

        setDocumentsOpen(false);
      }
    }


    document.addEventListener(
      "mousedown",
      handleOutsideClick
    );

    document.addEventListener(
      "keydown",
      handleEscape
    );


    return () => {
      document.removeEventListener(
        "mousedown",
        handleOutsideClick
      );

      document.removeEventListener(
        "keydown",
        handleEscape
      );
    };
  }, []);


  useEffect(() => {
    setMenuOpen(false);

    setDocumentsOpen(false);

    setStoppedSnapshot(
      null
    );
  }, [
    documentId,
    mode,
  ]);


  if (
    !open
    || chatId === null
    || documentId === null
    || !token
  ) {
    return null;
  }


  const displaySummary =
    stoppedSnapshot
    ?? selectedSummary;

  const displayGenerating =
    generating
    && stoppedSnapshot
      === null;


  function handleStopGeneration() {
    /*
      Freeze exactly what is currently visible BEFORE the hook/server
      has any chance to refresh or replace it.

      This makes Stop deterministic at the UI layer:
      - streamed content stays on screen,
      - no fallback to the previous completed result,
      - composer immediately returns to Regenerate state.
    */
    if (selectedSummary) {
      setStoppedSnapshot({
        ...selectedSummary,

        status:
          "cancelled",

        content:
          selectedSummary.content
            ? {
                ...selectedSummary.content,

                sections: [
                  ...selectedSummary
                    .content
                    .sections,
                ],
              }
            : null,
      });
    }

    stopGeneration();
  }


  async function handleRegenerate(
    requestedMode: SummaryMode
  ) {
    setStoppedSnapshot(
      null
    );

    await regenerateSummary(
      requestedMode
    );
  }


  function handleDocumentChange(
    nextDocument:
      SummaryDocument
  ) {
    if (
      nextDocument.id
      === documentId
    ) {
      setDocumentsOpen(false);

      return;
    }

    /*
      Switching documents only changes the active document.

      It intentionally does NOT generate or regenerate anything.
      The existing summary for that chat + document will simply
      be loaded by useDocumentSummary.
    */
    setDocumentsOpen(false);

    setMenuOpen(false);

    onSelectDocument?.(
      nextDocument
    );
  }


  function handleDelete() {
    if (
      !selectedSummary
      || deletingSummaryId !== null
    ) {
      return;
    }

    setMenuOpen(
      false
    );

    setDeleteError(
      null
    );

    clearError();

    setDeleteConfirmOpen(
      true
    );
  }


  function cancelDelete() {
    if (
      deletingSummaryId !== null
    ) {
      return;
    }

    setDeleteConfirmOpen(
      false
    );

    setDeleteError(
      null
    );

    clearError();
  }


  async function confirmDelete() {
    if (
      !selectedSummary
      || deletingSummaryId !== null
    ) {
      return;
    }

    try {
      setDeleteError(
        null
      );

      const deleted =
        await removeSummary();

      if (!deleted) {
        setDeleteError(
          `Could not delete this ${modeLabelLower}. Please try again.`
        );

        clearError();

        return;
      }

      setDeleteConfirmOpen(
        false
      );

      setDeleteError(
        null
      );

    } catch (deleteFailure) {
      console.error(
        `[${modeLabel.toUpperCase()} DELETE UI ERROR]`,
        deleteFailure
      );

      setDeleteError(
        deleteFailure instanceof Error
          && deleteFailure.message
            ? deleteFailure.message
            : `Could not delete this ${modeLabelLower}. Please try again.`
      );

      clearError();
    }
  }


  return (
    <section
      className="
        relative
        flex
        min-h-0
        flex-1
        flex-col
        bg-[var(--background)]
        text-[var(--foreground)]
      "
    >
      {deleteConfirmOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-document-output-title"
          className="
            absolute
            inset-0
            z-[100]
            flex
            items-center
            justify-center
            bg-black/45
            px-4
            backdrop-blur-[1px]
          "
        >
          <div
            className="
              w-full
              max-w-sm
              rounded-2xl
              border
              border-[var(--border)]
              bg-[var(--surface)]
              p-5
              shadow-2xl
              shadow-black/30
            "
          >
            <div
              className="
                flex
                items-start
                gap-3
              "
            >
              <div
                className="
                  flex
                  h-9
                  w-9
                  shrink-0
                  items-center
                  justify-center
                  rounded-full
                  bg-red-500/10
                  text-red-500
                "
              >
                <AlertTriangle
                  size={18}
                  strokeWidth={1.9}
                />
              </div>

              <div
                className="
                  min-w-0
                  flex-1
                "
              >
                <h2
                  id="delete-document-output-title"
                  className="
                    text-sm
                    font-semibold
                    text-[var(--text-primary)]
                  "
                >
                  Delete {modeLabelLower}?
                </h2>

                <p
                  className="
                    mt-1.5
                    text-xs
                    leading-5
                    text-[var(--text-muted)]
                  "
                >
                  This permanently deletes the current {modeLabelLower}
                  for
                  {" "}
                  <span
                    className="
                      font-medium
                      text-[var(--text-secondary)]
                    "
                  >
                    {currentFilename}
                  </span>
                  . This action cannot be undone.
                </p>
              </div>
            </div>

            {deleteError && (
              <div
                role="alert"
                className="
                  mt-4
                  rounded-xl
                  border
                  border-red-500/20
                  bg-red-500/10
                  px-3
                  py-2.5
                  text-xs
                  leading-5
                  text-red-500
                "
              >
                {deleteError}
              </div>
            )}

            <div
              className="
                mt-5
                flex
                items-center
                justify-end
                gap-2
              "
            >
              <button
                type="button"
                onClick={
                  cancelDelete
                }
                disabled={
                  deletingSummaryId !== null
                }
                className="
                  rounded-lg
                  px-3
                  py-2
                  text-xs
                  font-medium
                  text-[var(--text-secondary)]
                  transition
                  hover:bg-[var(--surface-hover)]
                  hover:text-[var(--text-primary)]
                  disabled:cursor-not-allowed
                  disabled:opacity-50
                "
              >
                Cancel
              </button>

              <button
                type="button"
                onClick={() => {
                  void confirmDelete();
                }}
                disabled={
                  deletingSummaryId !== null
                }
                className="
                  flex
                  items-center
                  gap-2
                  rounded-lg
                  bg-red-500
                  px-3
                  py-2
                  text-xs
                  font-semibold
                  text-white
                  transition
                  hover:bg-red-600
                  disabled:cursor-not-allowed
                  disabled:opacity-60
                "
              >
                {deletingSummaryId !== null ? (
                  <Loader2
                    size={14}
                    className="
                      animate-spin
                    "
                  />
                ) : (
                  <Trash2
                    size={14}
                  />
                )}

                {deletingSummaryId !== null
                  ? "Deleting..."
                  : `Delete ${modeLabelLower}`}
              </button>
            </div>
          </div>
        </div>
      )}

      <div
        className="
          mx-auto
          flex
          w-full
          max-w-4xl
          shrink-0
          items-center
          justify-between
          gap-3
          px-5
          pb-1
          pt-3
          sm:px-6
        "
      >
        <div
          ref={
            documentsRef
          }
          className="
            relative
            min-w-0
          "
        >
          <button
            type="button"
            onClick={() => {
              if (
                !onSelectDocument
                || documents.length
                <= 1
                || generating
              ) {
                return;
              }

              setDocumentsOpen(
                (current) =>
                  !current
              );
            }}
            disabled={
              !onSelectDocument
              || documents.length
              <= 1
              || generating
            }
            className="
              flex
              max-w-[min(70vw,38rem)]
              items-center
              gap-1.5
              text-left
              text-xs
              text-[var(--text-muted)]
              transition
              hover:text-[var(--text-secondary)]
              disabled:cursor-default
              disabled:hover:text-[var(--text-muted)]
            "
          >
            <span
              className="
                shrink-0
              "
            >
              Document:
            </span>

            <span
              title={
                currentFilename
              }
              className="
                truncate
                font-medium
                text-[var(--text-secondary)]
              "
            >
              {currentFilename}
            </span>

            {documents.length > 1 && (
              <ChevronDown
                size={13}
                className={`
                  shrink-0
                  transition-transform
                  duration-150

                  ${
                    documentsOpen
                      ? "rotate-180"
                      : ""
                  }
                `}
              />
            )}
          </button>


          {documentsOpen && (
            <div
              className="
                absolute
                left-0
                top-7
                z-50
                w-[min(28rem,82vw)]
                rounded-xl
                bg-[var(--menu)]
                p-1.5
                shadow-xl
                shadow-black/20
              "
            >
              {documents.map(
                (document) => {
                  const selected =
                    document.id
                    === documentId;

                  return (
                    <button
                      key={
                        document.id
                      }
                      type="button"
                      onClick={() =>
                        handleDocumentChange(
                          document
                        )
                      }
                      className={`
                        w-full
                        rounded-lg
                        px-3
                        py-2.5
                        text-left
                        text-sm
                        transition

                        ${
                          selected
                            ? "bg-[var(--surface-active)] text-[var(--text-primary)]"
                            : "text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
                        }
                      `}
                    >
                      <span
                        title={
                          document.filename
                        }
                        className="
                          block
                          truncate
                        "
                      >
                        {document.filename}
                      </span>
                    </button>
                  );
                }
              )}
            </div>
          )}
        </div>


        {displaySummary && (
          <div
            ref={
              menuRef
            }
            className="
              relative
              shrink-0
            "
          >
            <button
              type="button"
              onClick={() =>
                setMenuOpen(
                  (current) =>
                    !current
                )
              }
              title={`${modeLabel} options`}
              className="
                flex
                h-8
                w-8
                items-center
                justify-center
                rounded-full
                text-[var(--text-muted)]
                transition
                hover:bg-[var(--surface-hover)]
                hover:text-[var(--text-primary)]
              "
            >
              <MoreHorizontal
                size={17}
              />
            </button>


            {menuOpen && (
              <div
                className="
                  absolute
                  right-0
                  top-9
                  z-50
                  w-48
                  rounded-xl
                  bg-[var(--menu)]
                  p-1.5
                  shadow-xl
                  shadow-black/20
                "
              >
                <button
                  type="button"
                  onClick={
                    handleDelete
                  }
                  disabled={
                    deletingSummaryId
                    !== null
                  }
                  className="
                    flex
                    w-full
                    flex-nowrap
                    items-center
                    gap-2
                    whitespace-nowrap
                    rounded-lg
                    px-3
                    py-2
                    text-left
                    text-xs
                    font-medium
                    text-red-500
                    transition
                    hover:bg-red-500/10
                    disabled:opacity-50
                  "
                >
                  {deletingSummaryId
                  !== null ? (
                    <Loader2
                      size={14}
                      className="
                        shrink-0
                        animate-spin
                        text-red-500
                      "
                    />
                  ) : (
                    <Trash2
                      size={14}
                      className="
                        shrink-0
                        text-red-500
                      "
                    />
                  )}

                  <span
                    className="
                      whitespace-nowrap
                      text-red-500
                    "
                  >
                    Delete {modeLabelLower}
                  </span>
                </button>
              </div>
            )}
          </div>
        )}
      </div>


      <div
        className="
          min-h-0
          flex-1
          overflow-y-auto
        "
      >
        {loading ? (
          <div
            className="
              flex
              h-full
              items-center
              justify-center
            "
          >
            <div
              className="
                flex
                items-center
                gap-2
                text-sm
                text-[var(--text-muted)]
              "
            >
              <Loader2
                size={17}
                className="
                  animate-spin
                "
              />

              Loading {modeLabelLower}...
            </div>
          </div>

        ) : error && !deleteConfirmOpen ? (
          <div
            className="
              flex
              h-full
              items-center
              justify-center
              px-6
              text-center
            "
          >
            <div
              className="
                max-w-sm
              "
            >
              <p
                className="
                  text-sm
                  font-medium
                  text-[var(--text-primary)]
                "
              >
                Could not load {modeLabelLower}
              </p>

              <p
                dir="auto"
                className="
                  mt-2
                  text-xs
                  leading-5
                  text-[var(--text-muted)]
                "
              >
                {error}
              </p>

              <button
                type="button"
                onClick={
                  clearError
                }
                className="
                  mt-4
                  px-2
                  py-1
                  text-xs
                  font-medium
                  text-[var(--text-secondary)]
                  transition
                  hover:text-[var(--text-primary)]
                "
              >
                Dismiss
              </button>
            </div>
          </div>

        ) : !documentReady ? (
          <div
            className="
              flex
              h-full
              items-center
              justify-center
              px-6
              text-center
            "
          >
            <div
              className="
                max-w-sm
              "
            >
              <Loader2
                size={20}
                className="
                  mx-auto
                  animate-spin
                  text-[var(--primary)]
                "
              />

              <p
                className="
                  mt-4
                  text-sm
                  font-medium
                  text-[var(--text-primary)]
                "
              >
                Document is still processing
              </p>

              <p
                className="
                  mt-2
                  text-xs
                  leading-5
                  text-[var(--text-muted)]
                "
              >
                The {modeLabelLower} will be available
                when processing is complete.
              </p>
            </div>
          </div>

        ) : !displaySummary ? (
          <div
            className="
              flex
              h-full
              items-center
              justify-center
              px-6
            "
          >
            <div
              className="
                w-full
                max-w-2xl
                text-left
              "
            >
              <p
                className="
                  text-xs
                  font-medium
                  uppercase
                  tracking-[0.18em]
                  text-[var(--text-muted)]
                "
              >
                {modeLabel}
              </p>

              <h2
                className="
                  mt-3
                  text-2xl
                  font-semibold
                  leading-tight
                  text-[var(--text-primary)]
                  sm:text-3xl
                "
              >
                {
                  mode === "transcription"
                    ? "Read the document in detail"
                    : "Understand the document faster"
                }
              </h2>

              <p
                className="
                  mt-4
                  max-w-xl
                  text-[15px]
                  leading-7
                  text-[var(--text-secondary)]
                "
              >
                {
                  mode === "transcription"
                    ? (
                      "Transcription works through the file page by page and keeps the important detail, including tables, charts, images, equations, and technical content."
                    )
                    : (
                      "Summary condenses the file into its main ideas, key points, conclusions, and the most important takeaway from each section."
                    )
                }
              </p>

              <p
                className="
                  mt-3
                  max-w-xl
                  text-sm
                  leading-6
                  text-[var(--text-muted)]
                "
              >
                Add an optional instruction below if you want a specific focus,
                then press Generate.
              </p>
            </div>
          </div>

        ) : (
          <article
            className={`
              mx-auto
              w-full
              px-5
              pb-14
              pt-7
              sm:px-6

              ${
                mode === "transcription"
                  ? "max-w-4xl"
                  : "max-w-3xl"
              }
            `}
          >
            {displaySummary
              .content
              ?.title && (
              <h1
                dir="auto"
                className="
                  mb-8
                  text-2xl
                  font-semibold
                  leading-relaxed
                  text-[var(--text-primary)]
                "
              >
                {
                  displaySummary
                    .content
                    .title
                }
              </h1>
            )}


            <div
              className={
                mode === "transcription"
                  ? "space-y-5"
                  : "space-y-8"
              }
            >
              {displaySummary
                .content
                ?.sections
                ?.map(
                  (
                    block,
                    index
                  ) => (
                    <SummaryBlock
                      key={
                        `${displaySummary.id}-${index}`
                      }
                      block={
                        block
                      }
                      documentId={
                        documentId
                      }
                      token={
                        token
                      }
                    />
                  )
                )}


              {displayGenerating && (
                <div
                  className="
                    flex
                    items-center
                    gap-2
                    py-4
                    text-sm
                    text-[var(--text-muted)]
                  "
                >
                  <Loader2
                    size={15}
                    className="
                      animate-spin
                    "
                  />

                  Updating {modeLabelLower}...
                </div>
              )}


              {stoppedSnapshot && (
                <div
                  dir="auto"
                  className="
                    mt-6
                    rounded-xl
                    border
                    border-[var(--border)]
                    bg-[var(--surface)]
                    px-4
                    py-3
                  "
                >
                  <p
                    className="
                      text-sm
                      font-medium
                      text-[var(--text-primary)]
                    "
                  >
                    Generation stopped
                  </p>

                  <p
                    className="
                      mt-1
                      text-sm
                      leading-6
                      text-[var(--text-secondary)]
                    "
                  >
                    تم إيقاف التوليد هنا. إذا بدك تشوف {modeLabelLower} كامل،
                    اضغط Regenerate.
                  </p>
                </div>
              )}
            </div>
          </article>
        )}
      </div>


      {documentReady && (
        <SummaryAssistant
          token={
            token
          }
          chatId={
            chatId
          }
          documentId={
            documentId
          }
          mode={
            mode
          }
          uploading={
            uploading
          }
          fileInputRef={
            fileInputRef
          }
          onUpload={
            onUpload
          }
          regenerating={
            displayGenerating
          }
          onRegenerate={
            handleRegenerate
          }
          onStopGeneration={
            handleStopGeneration
          }
          onSummaryGenerated={(
            summary
          ) => {
            addGeneratedSummary({
              ...summary,
              mode,
            });
          }}
        />
      )}
    </section>
  );
}