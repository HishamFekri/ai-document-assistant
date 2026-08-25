"use client";

import {
  ChangeEvent,
  FormEvent,
  KeyboardEvent,
  RefObject,
} from "react";

import {
  FileText,
  Loader2,
  Plus,
  Send,
  Square,
  X,
} from "lucide-react";

import {
  ComposerAttachment,
} from "@/hooks/useChat";


type Props = {
  question: string;

  sending: boolean;
  uploading: boolean;

  attachment:
    ComposerAttachment | null;

  documentsProcessing: boolean;

  allowGeneralKnowledge: boolean;

  composerError:
    string | null;

  fileInputRef:
    RefObject<HTMLInputElement | null>;

  onQuestionChange: (
    value: string
  ) => void;

  onSetGeneralKnowledge: (
    value: boolean
  ) => void;

  onUpload: (
    event:
      ChangeEvent<HTMLInputElement>
  ) => void;

  onRemoveAttachment:
    () => void;

  onSubmit: (
    event: FormEvent
  ) => void;

  onStop?: () => void;
};


function attachmentStatusLabel(
  attachment:
    ComposerAttachment
) {
  if (
    attachment.status
    === "ready"
  ) {
    return "Ready";
  }

  if (
    attachment.status
    === "failed"
  ) {
    return "Failed";
  }

  const progress =
    attachment.progress
    ?? 0;

  if (
    progress > 0
  ) {
    return (
      `Processing ${progress}%`
    );
  }

  return "Processing...";
}


export default function ChatComposer({
  question,
  sending,
  uploading,
  attachment,
  documentsProcessing,
  allowGeneralKnowledge,
  composerError,
  fileInputRef,
  onQuestionChange,
  onSetGeneralKnowledge,
  onUpload,
  onRemoveAttachment,
  onSubmit,
  onStop,
}: Props) {
  const hasQuestion =
    Boolean(
      question.trim()
    );

  const hasAttachment =
    Boolean(
      attachment
    );

  const canSend =
    (
      hasQuestion
      || hasAttachment
    )
    && !sending
    && !uploading
    && !documentsProcessing;

  const canStop =
    sending
    && Boolean(
      onStop
    );


  function handleKeyDown(
    event:
      KeyboardEvent<HTMLTextAreaElement>
  ) {
    if (
      event.key !== "Enter"
      || event.shiftKey
    ) {
      return;
    }

    event.preventDefault();

    if (
      sending
    ) {
      return;
    }

    if (!canSend) {
      return;
    }

    event.currentTarget
      .form
      ?.requestSubmit();
  }


  return (
    <div
      className="
        bg-[var(--background)]
        px-4
        pb-5
        transition-colors
        duration-200
        sm:px-6
      "
    >
      <div
        className="
          mx-auto
          w-full
          max-w-3xl
        "
      >
        {attachment && (
          <div
            className="
              mb-2
              flex
              items-center
              justify-between
              gap-3
              rounded-2xl
              border
              border-[var(--border)]
              bg-[var(--surface)]
              px-3
              py-2.5
            "
          >
            <div
              className="
                flex
                min-w-0
                items-center
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
                  rounded-xl
                  bg-[var(--primary-soft)]
                  text-[var(--primary)]
                "
              >
                <FileText
                  size={17}
                />
              </div>


              <div
                className="
                  min-w-0
                "
              >
                <p
                  className="
                    truncate
                    text-xs
                    font-medium
                    text-[var(--text-primary)]
                  "
                >
                  {attachment.filename}
                </p>

                <div
                  className="
                    mt-0.5
                    flex
                    items-center
                    gap-1.5
                    text-[10px]
                    text-[var(--text-muted)]
                  "
                >
                  {attachment.status
                    === "processing"
                    && (
                      <Loader2
                        size={11}
                        className="
                          animate-spin
                        "
                      />
                    )}

                  <span>
                    {attachmentStatusLabel(
                      attachment
                    )}
                  </span>
                </div>
              </div>
            </div>


            <button
              type="button"
              onClick={
                onRemoveAttachment
              }
              disabled={
                sending
              }
              title="Remove attachment"
              className="
                flex
                h-8
                w-8
                shrink-0
                items-center
                justify-center
                rounded-full
                text-[var(--text-muted)]
                transition
                hover:bg-[var(--surface-hover)]
                hover:text-red-500
                disabled:cursor-not-allowed
                disabled:opacity-40
              "
            >
              <X
                size={15}
              />
            </button>
          </div>
        )}


        {composerError && (
          <p
            dir="auto"
            className="
              mb-2
              px-2
              text-start
              text-xs
              leading-5
              text-red-500
            "
          >
            {composerError}
          </p>
        )}


        <form
          onSubmit={
            onSubmit
          }
          className="
            rounded-[24px]
            border
            border-[var(--composer-border)]
            bg-[var(--composer)]
            px-2
            py-1.5
            shadow-[0_4px_18px_rgba(0,0,0,0.14)]
            transition-all
            duration-200
            focus-within:border-[var(--primary)]
            focus-within:shadow-[0_6px_24px_rgba(65,105,225,0.14)]
          "
        >
          <input
            ref={
              fileInputRef
            }
            type="file"
            accept=".pdf,.docx,.xlsx,.txt"
            onChange={
              onUpload
            }
            className="hidden"
          />


          <div
            className="
              flex
              min-h-12
              items-center
              gap-1.5
            "
          >
            <button
              type="button"
              onClick={() =>
                fileInputRef
                  .current
                  ?.click()
              }
              disabled={
                uploading
                || sending
              }
              title="Attach file"
              className="
                flex
                h-9
                w-9
                shrink-0
                items-center
                justify-center
                rounded-full
                text-[var(--text-secondary)]
                transition
                hover:bg-[var(--surface-hover)]
                hover:text-[var(--text-primary)]
                disabled:cursor-not-allowed
                disabled:opacity-40
              "
            >
              {uploading ? (
                <Loader2
                  size={18}
                  className="
                    animate-spin
                  "
                />
              ) : (
                <Plus
                  size={19}
                />
              )}
            </button>


            <textarea
              value={
                question
              }
              onChange={(
                event
              ) =>
                onQuestionChange(
                  event.target.value
                )
              }
              onKeyDown={
                handleKeyDown
              }
              rows={1}
              dir="auto"
              placeholder={
                sending
                  ? "Generating..."
                  : allowGeneralKnowledge
                    ? "Ask anything..."
                    : "Ask about your files..."
              }
              className="
                max-h-24
                min-h-9
                min-w-0
                flex-1
                resize-none
                bg-transparent
                px-2
                py-2
                text-start
                text-[14px]
                leading-5
                text-[var(--text-primary)]
                outline-none
                placeholder:text-[var(--text-muted)]
              "
            />


            <button
              type="button"
              onClick={() =>
                onSetGeneralKnowledge(
                  !allowGeneralKnowledge
                )
              }
              disabled={
                sending
              }
              title={
                allowGeneralKnowledge
                  ? "Using files and general knowledge"
                  : "Using attached files only"
              }
              aria-label={
                allowGeneralKnowledge
                  ? "Switch to files only"
                  : "Use files and general knowledge"
              }
              className={`
                flex
                h-8
                shrink-0
                items-center
                justify-center
                overflow-hidden
                rounded-full
                px-2.5
                text-[11px]
                font-medium
                whitespace-nowrap
                transition-all
                duration-200
                ease-out
                active:scale-[0.97]
                disabled:cursor-not-allowed
                disabled:opacity-50
                ${
                  allowGeneralKnowledge
                    ? "w-[124px] bg-[var(--primary-soft)] text-[var(--primary)] hover:brightness-110"
                    : "w-[56px] bg-[var(--surface-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                }
              `}
            >
              {allowGeneralKnowledge
                ? "Files + General"
                : "Files"}
            </button>


            {sending ? (
              <button
                type="button"
                onClick={
                  onStop
                }
                disabled={
                  !canStop
                }
                title="Stop generation"
                aria-label="Stop generation"
                className="
                  flex
                  h-9
                  w-9
                  shrink-0
                  items-center
                  justify-center
                  rounded-full
                  bg-[var(--primary)]
                  text-white
                  transition
                  hover:opacity-90
                  active:scale-95
                  disabled:cursor-not-allowed
                  disabled:opacity-50
                "
              >
                <Square
                  size={14}
                  fill="currentColor"
                />
              </button>

            ) : (
              <button
                type="submit"
                disabled={
                  !canSend
                }
                title="Send"
                aria-label="Send"
                className="
                  flex
                  h-9
                  w-9
                  shrink-0
                  items-center
                  justify-center
                  rounded-full
                  bg-[var(--primary)]
                  text-white
                  transition
                  hover:opacity-90
                  active:scale-95
                  disabled:cursor-not-allowed
                  disabled:opacity-40
                "
              >
                <Send
                  size={16}
                />
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}