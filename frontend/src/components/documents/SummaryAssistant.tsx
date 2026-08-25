"use client";

import {
  useRef,
} from "react";

import type {
  ChangeEvent,
  FormEvent,
  KeyboardEvent,
  MouseEvent,
  RefObject,
} from "react";

import {
  Loader2,
  Plus,
  Square,
} from "lucide-react";

import type {
  GeneratedSummary,
} from "@/lib/summary-assistant-api";

import type {
  SummaryMode,
} from "@/lib/summary-api";

import {
  useSummaryAssistant,
} from "@/hooks/useSummaryAssistant";


type Props = {
  token: string | null;

  chatId:
    number | null;

  documentId:
    number | null;

  mode: SummaryMode;

  regenerating?: boolean;

  uploading?: boolean;

  fileInputRef?:
    RefObject<HTMLInputElement | null>;

  onUpload?: (
    event:
      ChangeEvent<HTMLInputElement>
  ) => void;

  onRegenerate?: (
    mode: SummaryMode
  ) => void | Promise<unknown>;

  onStopGeneration?: () => void;

  onSummaryGenerated?: (
    summary: GeneratedSummary
  ) => void;
};


export default function SummaryAssistant({
  token,
  chatId,
  documentId,
  mode,
  regenerating = false,
  uploading = false,
  fileInputRef,
  onUpload,
  onRegenerate,
  onStopGeneration,
  onSummaryGenerated,
}: Props) {
  const {
    input,
    setInput,
    loading,
    sending,
    error,
    sendMessage,
  } = useSummaryAssistant({
    token,
    chatId,
    documentId,
    onSummaryGenerated,
  });


  const stopGuardRef =
    useRef(false);


  const unavailable =
    !token
    || chatId === null
    || documentId === null;

  const working =
    sending
    || regenerating
    || uploading;


  async function runGeneration() {
    if (
      stopGuardRef.current
      || unavailable
      || working
      || loading
      || !onRegenerate
    ) {
      return;
    }

    const instruction =
      input.trim();

    if (instruction) {
      const result =
        await sendMessage(
          instruction
        );

      if (!result) {
        return;
      }
    }

    await onRegenerate(
      mode
    );
  }


  async function handleSubmit(
    event: FormEvent
  ) {
    event.preventDefault();

    if (
      stopGuardRef.current
    ) {
      return;
    }

    await runGeneration();
  }


  function handleStopClick(
    event:
      MouseEvent<HTMLButtonElement>
  ) {
    /*
      The Stop button lives inside the same <form> as Regenerate.

      When Stop changes `regenerating` to false, React immediately
      swaps this button for the submit/Regenerate button. We block
      any submit/click continuation from that same interaction so
      Stop can never accidentally become Regenerate.
    */
    event.preventDefault();
    event.stopPropagation();

    stopGuardRef.current =
      true;

    onStopGeneration?.();

    window.setTimeout(
      () => {
        stopGuardRef.current =
          false;
      },
      500
    );
  }


  function handleKeyDown(
    event:
      KeyboardEvent<HTMLTextAreaElement>
  ) {
    if (
      event.key === "Enter"
      && !event.shiftKey
    ) {
      event.preventDefault();

      void runGeneration();
    }
  }


  const canGenerate =
    !unavailable
    && !loading
    && !working
    && Boolean(
      onRegenerate
    );


  return (
    <div
      className="
        shrink-0
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
        {error && (
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
            {error}
          </p>
        )}


        <form
          onSubmit={
            handleSubmit
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
          {fileInputRef
          && onUpload && (
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
          )}


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
                  ?.current
                  ?.click()
              }
              disabled={
                unavailable
                || uploading
                || regenerating
                || !fileInputRef
                || !onUpload
              }
              title="Attach file"
              aria-label="Attach file"
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
                input
              }
              onChange={(
                event
              ) =>
                setInput(
                  event.target.value
                )
              }
              onKeyDown={
                handleKeyDown
              }
              disabled={
                unavailable
                || working
                || loading
              }
              rows={1}
              dir="auto"
              placeholder={
                regenerating
                  ? (
                      mode
                      === "transcription"
                        ? "Generating transcription..."
                        : "Generating summary..."
                    )
                  : uploading
                    ? "Uploading file..."
                    : (
                        mode
                        === "transcription"
                          ? "Ask for changes to the transcription..."
                          : "Ask for changes to the summary..."
                      )
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
                disabled:cursor-not-allowed
                disabled:opacity-60
              "
            />


            {regenerating ? (
              <button
                type="button"
                onMouseDown={(
                  event
                ) => {
                  event.preventDefault();
                  event.stopPropagation();
                }}
                onClick={
                  handleStopClick
                }
                disabled={
                  !onStopGeneration
                }
                title={
                  mode
                  === "transcription"
                    ? "Stop transcription"
                    : "Stop summary"
                }
                aria-label={
                  mode
                  === "transcription"
                    ? "Stop transcription"
                    : "Stop summary"
                }
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
                  !canGenerate
                }
                title={
                  mode
                  === "transcription"
                    ? "Generate transcription"
                    : "Generate summary"
                }
                aria-label={
                  mode
                  === "transcription"
                    ? "Generate transcription"
                    : "Generate summary"
                }
                className="
                  flex
                  h-9
                  shrink-0
                  items-center
                  justify-center
                  rounded-full
                  bg-[var(--primary)]
                  px-4
                  text-xs
                  font-medium
                  text-white
                  transition
                  hover:opacity-90
                  active:scale-95
                  disabled:cursor-not-allowed
                  disabled:opacity-40
                "
              >
                {sending ? (
                  <Loader2
                    size={16}
                    className="
                      animate-spin
                    "
                  />
                ) : (
                  "Generate"
                )}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}