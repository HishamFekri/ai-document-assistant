"use client";

import {
  FileText,
  X,
} from "lucide-react";

import {
  Document,
} from "@/types/chat";


type Props = {
  document: Document;
  onRemove: () => void;
};


export default function DocumentRow({
  document,
  onRemove,
}: Props) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-neutral-200 px-3 py-2.5">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-neutral-100">
        <FileText
          size={16}
        />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-3">
          <p className="truncate text-xs font-medium">
            {document.filename}
          </p>

          <DocumentStatus
            document={
              document
            }
          />
        </div>

        {document.processing_status ===
          "processing" && (
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-neutral-100">
            <div
              className="h-full rounded-full bg-black transition-all duration-500"
              style={{
                width:
                  `${document.processing_progress}%`,
              }}
            />
          </div>
        )}
      </div>

      <button
        onClick={
          onRemove
        }
        title="Remove from chat"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-neutral-400 transition hover:bg-neutral-100 hover:text-red-500"
      >
        <X
          size={15}
        />
      </button>
    </div>
  );
}


function DocumentStatus({
  document,
}: {
  document: Document;
}) {
  if (
    document.processing_status ===
    "ready"
  ) {
    return (
      <span className="shrink-0 text-[11px] font-medium text-emerald-600">
        Ready
      </span>
    );
  }

  if (
    document.processing_status ===
    "failed"
  ) {
    return (
      <span className="shrink-0 text-[11px] font-medium text-red-500">
        Failed
      </span>
    );
  }

  return (
    <span className="shrink-0 text-[11px] font-medium text-neutral-400">
      {document.processing_progress}%
    </span>
  );
}