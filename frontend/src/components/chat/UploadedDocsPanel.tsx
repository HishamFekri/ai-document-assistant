"use client";

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


type Props = {
  open: boolean;

  documents: Document[];

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


  if (extension === "pdf") {
    return (
      <div
        className="
          flex
          h-10
          w-10
          shrink-0
          items-center
          justify-center
          rounded-xl
          bg-red-500/10
        "
      >
        <FileText
          size={20}
          strokeWidth={1.8}
          className="
            text-red-500
          "
        />
      </div>
    );
  }


  if (
    extension === "doc"
    || extension === "docx"
  ) {
    return (
      <div
        className="
          flex
          h-10
          w-10
          shrink-0
          items-center
          justify-center
          rounded-xl
          bg-blue-500/10
        "
      >
        <FileType2
          size={20}
          strokeWidth={1.8}
          className="
            text-blue-500
          "
        />
      </div>
    );
  }


  if (
    extension === "xls"
    || extension === "xlsx"
  ) {
    return (
      <div
        className="
          flex
          h-10
          w-10
          shrink-0
          items-center
          justify-center
          rounded-xl
          bg-emerald-500/10
        "
      >
        <FileSpreadsheet
          size={20}
          strokeWidth={1.8}
          className="
            text-emerald-500
          "
        />
      </div>
    );
  }


  if (extension === "txt") {
    return (
      <div
        className="
          flex
          h-10
          w-10
          shrink-0
          items-center
          justify-center
          rounded-xl
          bg-violet-500/10
        "
      >
        <FileText
          size={20}
          strokeWidth={1.8}
          className="
            text-violet-500
          "
        />
      </div>
    );
  }


  return (
    <div
      className="
        flex
        h-10
        w-10
        shrink-0
        items-center
        justify-center
        rounded-xl
        bg-[var(--surface)]
      "
    >
      <File
        size={20}
        strokeWidth={1.8}
        className="
          text-[var(--text-muted)]
        "
      />
    </div>
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


export default function UploadedDocsPanel({
  open,
  documents,
  onClose,
  onRemove,
  onOpenSummary,
}: Props) {
  if (!open) {
    return null;
  }


  return (
    <aside
      className="
        absolute
        right-0
        top-0
        z-40
        flex
        h-full
        w-[320px]
        flex-col

        border-l
        border-[var(--border)]

        bg-[var(--background)]
        text-[var(--text-primary)]

        shadow-2xl
        shadow-black/30

        transition-colors
        duration-200

        xl:relative
        xl:shadow-none
      "
    >
      <div
        className="
          flex
          min-h-16
          items-center
          justify-between
          px-5
        "
      >
        <div>
          <h2
            className="
              text-[15px]
              font-semibold
              text-[var(--text-primary)]
            "
          >
            Documents
          </h2>

          <p
            className="
              mt-0.5
              text-xs
              text-[var(--text-muted)]
            "
          >
            {documents.length}
            {" "}
            {documents.length === 1
              ? "file"
              : "files"}
          </p>
        </div>


        <button
          type="button"
          onClick={
            onClose
          }
          title="Close"
          className="
            flex
            h-9
            w-9
            items-center
            justify-center
            rounded-full

            text-red-500

            transition-all
            duration-200

            hover:bg-red-500/10
            hover:text-red-400

            active:scale-95
          "
        >
          <X
            size={18}
          />
        </button>
      </div>


      <div
        className="
          min-h-0
          flex-1
          overflow-y-auto
          px-3
          pb-4
        "
      >
        {documents.length === 0 ? (
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
            <div>
              <div
                className="
                  mx-auto
                  mb-3
                  flex
                  h-12
                  w-12
                  items-center
                  justify-center
                  rounded-2xl
                  bg-[var(--primary-soft)]
                "
              >
                <FileText
                  size={21}
                  className="
                    text-[var(--primary)]
                  "
                />
              </div>

              <p
                className="
                  text-sm
                  font-medium
                  text-[var(--text-primary)]
                "
              >
                No documents yet
              </p>

              <p
                className="
                  mt-1
                  text-xs
                  leading-5
                  text-[var(--text-muted)]
                "
              >
                Attach files using the
                + button in the chat.
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            {documents.map(
              (
                document
              ) => {
                const processing =
                  document.processing_status
                  === "processing";

                const failed =
                  document.processing_status
                  === "failed";

                const ready =
                  !processing
                  && !failed;

                const fileLabel =
                  getFileLabel(
                    document.filename
                  );


                return (
                  <div
                    key={
                      document.id
                    }
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
                      flex
                      items-center
                      gap-3
                      rounded-2xl
                      px-3
                      py-3
                      transition

                      ${
                        ready
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
                          text-[var(--text-primary)]
                        "
                      >
                        {document.filename}
                      </p>


                      <div
                        className="
                          mt-1
                          flex
                          items-center
                          gap-1.5
                        "
                      >
                        <span
                          className="
                            text-[11px]
                            text-[var(--text-muted)]
                          "
                        >
                          {fileLabel}
                        </span>

                        <span
                          className="
                            text-[10px]
                            text-[var(--border)]
                          "
                        >
                          •
                        </span>


                        {processing ? (
                          <span
                            className="
                              flex
                              items-center
                              gap-1
                              text-[11px]
                              font-medium
                              text-amber-500
                            "
                          >
                            <Loader2
                              size={11}
                              className="
                                animate-spin
                              "
                            />

                            {document.processing_progress
                              ?? 0}
                            %
                          </span>

                        ) : failed ? (
                          <span
                            className="
                              text-[11px]
                              font-medium
                              text-red-500
                            "
                          >
                            Failed
                          </span>

                        ) : (
                          <span
                            className="
                              flex
                              items-center
                              gap-1
                              text-[11px]
                              font-medium
                              text-emerald-500
                            "
                          >
                            <span
                              className="
                                h-1.5
                                w-1.5
                                rounded-full
                                bg-emerald-500
                              "
                            />

                            Ready
                          </span>
                        )}
                      </div>
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
                      className="
                        flex
                        h-8
                        w-8
                        shrink-0
                        items-center
                        justify-center
                        rounded-lg

                        text-red-500

                        opacity-70

                        transition-all
                        duration-200

                        hover:bg-red-500/10
                        hover:text-red-400
                        hover:opacity-100

                        active:scale-95
                      "
                    >
                      <Trash2
                        size={14}
                      />
                    </button>
                  </div>
                );
              }
            )}
          </div>
        )}
      </div>
    </aside>
  );
}