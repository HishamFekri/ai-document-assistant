"use client";

import {
  Check,
  CheckCircle2,
  Clipboard,
  FileText,
  Loader2,
  RefreshCw,
  Sparkles,
  TriangleAlert,
} from "lucide-react";

import {
  useMemo,
  useState,
} from "react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

import "katex/dist/katex.min.css";

import SourceImage from "@/components/chat/SourceImage";

import {
  Message,
  Source,
} from "@/types/chat";


type Props = {
  message: Message;

  onRetry?: (
    message: Message
  ) => void | Promise<void>;

  retryDisabled?: boolean;
};


function formatMessageTime(
  createdAt: string
) {
  if (!createdAt) {
    return "";
  }

  const date =
    new Date(createdAt);

  if (
    Number.isNaN(
      date.getTime()
    )
  ) {
    return "";
  }

  return (
    new Intl.DateTimeFormat(
      undefined,
      {
        hour: "2-digit",
        minute: "2-digit",
      }
    ).format(date)
  );
}


function attachmentLabel(
  status: string,
  progress: number
) {
  if (
    status
    === "ready"
  ) {
    return "Ready";
  }

  if (
    status
    === "failed"
  ) {
    return "Failed";
  }

  if (
    progress > 0
  ) {
    return (
      `Processing ${progress}%`
    );
  }

  return "Processing...";
}


function cleanAssistantContent(
  content: string
) {
  return content
    .replace(
      /\s*\[S\d+\](?:\s*\[S\d+\])*/gi,
      ""
    )
    .replace(
      /^[ \t]*(?:#{1,6}[ \t]*)?(?:Sources|References|Citations)[ \t]*:?[ \t]*$/gim,
      ""
    )
    .replace(
      /\n{3,}/g,
      "\n\n"
    )
    .trim();
}


function isRenderableVisual(
  source: Source
) {
  return (
    typeof source.asset_url
      === "string"
    && source.asset_url.trim().length
      > 0
  );
}


function dedupeVisualSources(
  sources: Source[]
) {
  const seen = new Set<string>();

  return sources.filter(
    (source) => {
      if (
        !isRenderableVisual(
          source
        )
      ) {
        return false;
      }

      const key =
        String(
          source.asset_url
        );

      if (
        seen.has(
          key
        )
      ) {
        return false;
      }

      seen.add(
        key
      );

      return true;
    }
  );
}


function AssistantMarkdown({
  content,
}: {
  content: string;
}) {
  return (
    <div
      dir="auto"
      style={{
        unicodeBidi:
          "plaintext",
      }}
      className="
        min-w-0
        text-start
        text-[15px]
        leading-7
        text-[var(--text-primary)]
      "
    >
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          remarkMath,
        ]}
        rehypePlugins={[
          rehypeKatex,
        ]}
        components={{
          p({
            children,
          }) {
            return (
              <p
                className="
                  mb-3
                  last:mb-0
                "
              >
                {children}
              </p>
            );
          },

          h1({
            children,
          }) {
            return (
              <h1
                className="
                  mb-3
                  mt-5
                  text-xl
                  font-semibold
                  first:mt-0
                "
              >
                {children}
              </h1>
            );
          },

          h2({
            children,
          }) {
            return (
              <h2
                className="
                  mb-3
                  mt-5
                  text-lg
                  font-semibold
                  first:mt-0
                "
              >
                {children}
              </h2>
            );
          },

          h3({
            children,
          }) {
            return (
              <h3
                className="
                  mb-2
                  mt-4
                  text-[16px]
                  font-semibold
                  first:mt-0
                "
              >
                {children}
              </h3>
            );
          },

          ul({
            children,
          }) {
            return (
              <ul
                className="
                  mb-3
                  list-disc
                  space-y-1
                  ps-6
                "
              >
                {children}
              </ul>
            );
          },

          ol({
            children,
          }) {
            return (
              <ol
                className="
                  mb-3
                  list-decimal
                  space-y-1
                  ps-6
                "
              >
                {children}
              </ol>
            );
          },

          li({
            children,
          }) {
            return (
              <li
                className="
                  ps-1
                "
              >
                {children}
              </li>
            );
          },

          strong({
            children,
          }) {
            return (
              <strong
                className="
                  font-semibold
                  text-[var(--text-primary)]
                "
              >
                {children}
              </strong>
            );
          },

          blockquote({
            children,
          }) {
            return (
              <blockquote
                className="
                  my-4
                  border-s-2
                  border-[var(--border)]
                  ps-4
                  text-[var(--text-secondary)]
                "
              >
                {children}
              </blockquote>
            );
          },

          table({
            children,
          }) {
            return (
              <div
                className="
                  my-4
                  w-full
                  overflow-x-auto
                  rounded-xl
                  border
                  border-[var(--border)]
                "
              >
                <table
                  className="
                    w-full
                    min-w-[520px]
                    border-collapse
                    text-sm
                  "
                >
                  {children}
                </table>
              </div>
            );
          },

          thead({
            children,
          }) {
            return (
              <thead
                className="
                  bg-[var(--surface-hover)]
                "
              >
                {children}
              </thead>
            );
          },

          th({
            children,
          }) {
            return (
              <th
                className="
                  border-b
                  border-[var(--border)]
                  px-3
                  py-2
                  text-start
                  font-semibold
                "
              >
                {children}
              </th>
            );
          },

          td({
            children,
          }) {
            return (
              <td
                className="
                  border-b
                  border-[var(--border)]
                  px-3
                  py-2
                  align-top
                  text-[var(--text-secondary)]
                  last:border-b-0
                "
              >
                {children}
              </td>
            );
          },

          pre({
            children,
          }) {
            return (
              <pre
                dir="ltr"
                className="
                  my-4
                  overflow-x-auto
                  rounded-xl
                  bg-[var(--surface-hover)]
                  p-4
                  text-left
                  text-[13px]
                  leading-6
                "
              >
                {children}
              </pre>
            );
          },

          code({
            children,
            className,
            ...props
          }) {
            const isBlock =
              typeof className
                === "string"
              && className.includes(
                "language-"
              );

            if (isBlock) {
              return (
                <code
                  className={
                    className
                  }
                  {...props}
                >
                  {children}
                </code>
              );
            }

            return (
              <code
                dir="ltr"
                className="
                  rounded-md
                  bg-[var(--surface-hover)]
                  px-1.5
                  py-0.5
                  font-mono
                  text-[0.9em]
                "
                {...props}
              >
                {children}
              </code>
            );
          },

          hr() {
            return (
              <hr
                className="
                  my-5
                  border-[var(--border)]
                "
              />
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}


function AssistantVisuals({
  sources,
}: {
  sources: Source[];
}) {
  if (
    sources.length === 0
  ) {
    return null;
  }

  return (
    <div
      className="
        mt-5
        grid
        gap-3
      "
    >
      {sources.map(
        (
          source,
          index
        ) => (
          <div
            key={
              String(
                source.asset_url
              )
            }
            className="
              overflow-hidden
              rounded-2xl
              border
              border-[var(--border)]
              bg-[var(--surface)]
            "
          >
            <SourceImage
              imageUrl={
                String(
                  source.asset_url
                )
              }
              alt={
                source.filename
                ? `${source.filename} image ${index + 1}`
                : `Document image ${index + 1}`
              }
            />
          </div>
        )
      )}
    </div>
  );
}


export default function ChatMessage({
  message,
  onRetry,
  retryDisabled = false,
}: Props) {
  const isUser =
    message.role
    === "user";

  const [
    copied,
    setCopied,
  ] = useState(false);


  const time =
    formatMessageTime(
      message.created_at
    );

  const documents =
    message.documents
    ?? [];

  const assistantContent =
    useMemo(
      () =>
        cleanAssistantContent(
          message.content
          ?? ""
        ),
      [
        message.content,
      ]
    );

  const visualSources =
    useMemo(
      () =>
        dedupeVisualSources(
          message.sources
          ?? []
        ),
      [
        message.sources,
      ]
    );


  async function copyMessage() {
    if (!message.content) {
      return;
    }

    try {
      await navigator
        .clipboard
        .writeText(
          isUser
            ? message.content
            : assistantContent
        );

      setCopied(true);

      window.setTimeout(
        () => {
          setCopied(false);
        },
        1400
      );

    } catch (error) {
      console.error(
        "Could not copy message",
        error
      );
    }
  }


  if (isUser) {
    return (
      <div
        className="
          group
          flex
          w-full
          justify-end
        "
      >
        <div
          className="
            flex
            max-w-[82%]
            flex-col
            items-end
          "
        >
          {documents.length > 0 && (
            <div
              className="
                mb-2
                flex
                flex-col
                items-end
                gap-2
              "
            >
              {documents.map(
                (
                  document
                ) => (
                  <div
                    key={
                      document.id
                    }
                    className="
                      flex
                      w-fit
                      max-w-[320px]
                      items-center
                      gap-3
                      rounded-2xl
                      border
                      border-[var(--border)]
                      bg-[var(--surface)]
                      px-3
                      py-2.5
                      text-[var(--text-primary)]
                      shadow-sm
                    "
                  >
                    <div
                      className="
                        flex
                        h-10
                        w-10
                        shrink-0
                        items-center
                        justify-center
                        rounded-xl
                        bg-[var(--primary-soft)]
                        text-[var(--primary)]
                      "
                    >
                      <FileText
                        size={19}
                      />
                    </div>


                    <div
                      className="
                        min-w-0
                        flex-1
                      "
                    >
                      <p
                        className="
                          truncate
                          text-sm
                          font-medium
                        "
                      >
                        {
                          document
                            .filename
                        }
                      </p>


                      <div
                        className="
                          mt-0.5
                          flex
                          items-center
                          gap-1.5
                          text-[11px]
                          text-[var(--text-muted)]
                        "
                      >
                        {document
                          .processing_status
                          === "ready" ? (
                          <CheckCircle2
                            size={13}
                            className="
                              text-emerald-500
                            "
                          />

                        ) : document
                          .processing_status
                          === "failed" ? (
                          <TriangleAlert
                            size={13}
                            className="
                              text-red-500
                            "
                          />

                        ) : (
                          <Loader2
                            size={13}
                            className="
                              animate-spin
                              text-[var(--primary)]
                            "
                          />
                        )}


                        <span>
                          {attachmentLabel(
                            document
                              .processing_status,

                            document
                              .processing_progress
                              ?? 0
                          )}
                        </span>
                      </div>
                    </div>
                  </div>
                )
              )}
            </div>
          )}


          {message.content && (
            <div
              className="
                flex
                w-full
                justify-end
              "
            >
              <div
                dir="auto"
                style={{
                  unicodeBidi:
                    "plaintext",
                }}
                className="
                  w-fit
                  max-w-full
                  whitespace-pre-wrap
                  rounded-[22px]
                  rounded-br-[7px]
                  bg-[var(--primary)]
                  px-4
                  py-2.5
                  text-start
                  text-[15px]
                  leading-6
                  text-white
                "
              >
                {message.content}
              </div>
            </div>
          )}


          <div
            className="
              mt-1
              flex
              min-h-7
              items-center
              justify-end
              gap-1
              pr-1
            "
          >
            {time && (
              <span
                className="
                  mr-1
                  text-[10px]
                  text-[var(--text-muted)]
                "
              >
                {time}
              </span>
            )}


            {message.status
              === "failed" && (
              <div
                className="
                  flex
                  items-center
                  gap-1.5
                  text-[11px]
                  text-red-500
                "
                title={
                  message.error
                  || "Message failed"
                }
              >
                <TriangleAlert
                  size={13}
                />

                <span>
                  Failed
                </span>
              </div>
            )}


            {message.status
              === "failed"
              && onRetry && (
              <button
                type="button"
                onClick={() => {
                  void onRetry(
                    message
                  );
                }}
                disabled={
                  retryDisabled
                }
                title="Retry"
                className="
                  flex
                  h-7
                  items-center
                  justify-center
                  gap-1.5
                  rounded-lg
                  px-2
                  text-[11px]
                  font-medium
                  text-[var(--text-muted)]
                  transition-all
                  duration-150
                  hover:bg-[var(--surface-hover)]
                  hover:text-[var(--primary)]
                  disabled:cursor-not-allowed
                  disabled:opacity-40
                "
              >
                <RefreshCw
                  size={13}
                />

                Retry
              </button>
            )}


            {message.content && (
              <button
                type="button"
                onClick={
                  copyMessage
                }
                title="Copy"
                className="
                  flex
                  h-7
                  w-7
                  items-center
                  justify-center
                  rounded-lg
                  text-[var(--text-muted)]
                  opacity-0
                  transition-all
                  duration-150
                  hover:bg-[var(--surface-hover)]
                  hover:text-[var(--primary)]
                  group-hover:opacity-100
                "
              >
                {copied ? (
                  <Check
                    size={14}
                    className="
                      text-emerald-500
                    "
                  />
                ) : (
                  <Clipboard
                    size={14}
                  />
                )}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }


  return (
    <div
      className="
        group
        w-full
      "
    >
      <div
        className="
          flex
          gap-3
        "
      >
        <div
          className="
            mt-0.5
            flex
            h-7
            w-7
            shrink-0
            items-center
            justify-center
            rounded-full
            bg-[var(--primary-soft)]
            text-[var(--primary)]
          "
        >
          <Sparkles
            size={14}
          />
        </div>


        <div
          className="
            min-w-0
            flex-1
          "
        >
          {assistantContent ? (
            <AssistantMarkdown
              content={
                assistantContent
              }
            />

          ) : (
            <div
              className="
                flex
                h-7
                items-center
                gap-1.5
              "
            >
              <span
                className="
                  h-1.5
                  w-1.5
                  animate-pulse
                  rounded-full
                  bg-[var(--primary)]
                "
              />

              <span
                className="
                  h-1.5
                  w-1.5
                  animate-pulse
                  rounded-full
                  bg-[var(--primary)]
                  [animation-delay:150ms]
                "
              />

              <span
                className="
                  h-1.5
                  w-1.5
                  animate-pulse
                  rounded-full
                  bg-[var(--primary)]
                  [animation-delay:300ms]
                "
              />
            </div>
          )}


          <AssistantVisuals
            sources={
              visualSources
            }
          />


          <div
            className="
              relative
              mt-1
              flex
              min-h-7
              items-center
              gap-1
            "
          >
            {time && (
              <span
                className="
                  mr-1
                  text-[10px]
                  text-[var(--text-muted)]
                "
              >
                {time}
              </span>
            )}


            {message.content && (
              <button
                type="button"
                onClick={
                  copyMessage
                }
                title="Copy"
                className="
                  flex
                  h-7
                  w-7
                  shrink-0
                  items-center
                  justify-center
                  rounded-lg
                  text-[var(--text-muted)]
                  transition-all
                  duration-150
                  hover:bg-[var(--surface-hover)]
                  hover:text-[var(--primary)]
                "
              >
                {copied ? (
                  <Check
                    size={14}
                    className="
                      text-emerald-500
                    "
                  />
                ) : (
                  <Clipboard
                    size={14}
                  />
                )}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}