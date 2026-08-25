"use client";

import {
  FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  useRouter,
} from "next/navigation";

import {
  Chat,
  Document,
  Message,
  Source,
} from "@/types/chat";

import {
  ComposerAttachment,
} from "@/hooks/useChat";


export type SummaryGeneratedEvent = {
  documentId: number;
  summaryId: number;
  version: number;
};


export type ChatTitleGeneratedEvent = {
  title: string;
};


type Params = {
  chatId: number;

  chat: Chat | null;

  messages: Message[];

  setMessages: React.Dispatch<
    React.SetStateAction<Message[]>
  >;

  refreshMessages:
    () => Promise<void>;

  getToken:
    () => string | null;

  createPersistedChat?: (
    () => Promise<Chat>
  );

  attachDocumentToChat?: (
    chatId: number,
    documentId: number
  ) => Promise<Chat>;

  attachment:
    ComposerAttachment | null;

  clearComposerAttachment:
    () => void;

  onSummaryGenerated?: (
    event: SummaryGeneratedEvent
  ) => void | Promise<void>;

  onChatTitleGenerated?: (
    event: ChatTitleGeneratedEvent
  ) => void | Promise<void>;
};


type RunQuestionOptions = {
  questionText: string;

  documentIds?: number[];

  optimisticDocuments?: Document[];

  outgoingAttachment?:
    ComposerAttachment | null;

  clearQuestion?: boolean;

  allowGeneralKnowledgeOverride?: boolean;
};


function fileTypeFromName(
  filename: string
) {
  const extension =
    filename
      .split(".")
      .pop()
      ?.toLowerCase();

  return extension || null;
}


function buildOptimisticDocument(
  attachment:
    ComposerAttachment,
  temporaryId: number
): Document {
  return {
    id:
      attachment.documentId
      ?? temporaryId,

    filename:
      attachment.filename,

    file_type:
      fileTypeFromName(
        attachment.filename
      ),

    pages_count:
      null,

    processing_status:
      attachment.status,

    processing_stage:
      attachment.status,

    processing_progress:
      attachment.progress
      ?? 0,

    processing_error:
      attachment.error
      ?? null,

    created_at:
      new Date()
        .toISOString(),
  };
}


function isAbortError(
  error: unknown
) {
  return (
    error instanceof DOMException
    && error.name === "AbortError"
  );
}


export function useChatStream({
  chatId,
  chat,
  messages,
  setMessages,
  refreshMessages,
  getToken,
  createPersistedChat,
  attachDocumentToChat,
  attachment,
  clearComposerAttachment,
  onSummaryGenerated,
  onChatTitleGenerated,
}: Params) {
  const router =
    useRouter();

  const [
    question,
    setQuestion,
  ] = useState("");

  const [
    sending,
    setSending,
  ] = useState(false);

  const [
    composerError,
    setComposerError,
  ] = useState<string | null>(
    null
  );

  const [
    allowGeneralKnowledge,
    setAllowGeneralKnowledge,
  ] = useState(false);


  const attachmentRef =
    useRef<
      ComposerAttachment | null
    >(attachment);

  const sendingRef =
    useRef(false);

  const abortControllerRef =
    useRef<
      AbortController | null
    >(null);

  const stopRequestedRef =
    useRef(false);


  useEffect(() => {
    attachmentRef.current =
      attachment;
  }, [
    attachment,
  ]);


  useEffect(() => {
    setComposerError(null);

    return () => {
      stopRequestedRef.current =
        true;

      abortControllerRef.current
        ?.abort();

      abortControllerRef.current =
        null;

      sendingRef.current =
        false;
    };
  }, [
    chatId,
  ]);


  useEffect(() => {
    /*
      Clear an old composer error only when the user actually
      changes to a usable input state.

      Do NOT clear errors merely because the chat has documents.
      A failed document is still present in chat.documents, and
      clearing here would make processing errors disappear instantly.
    */
    if (
      allowGeneralKnowledge
      || (
        attachment
        && attachment.status
        !== "failed"
      )
    ) {
      setComposerError(null);
    }
  }, [
    allowGeneralKnowledge,
    attachment?.localId,
  ]);


  async function waitForDocumentId(
    localId: string,
  ): Promise<number> {
    const started =
      Date.now();

    while (
      Date.now()
      - started
      < 120000
    ) {
      if (
        stopRequestedRef.current
      ) {
        throw new DOMException(
          "Generation stopped",
          "AbortError"
        );
      }

      const current =
        attachmentRef.current;

      if (
        !current
        || current.localId
        !== localId
      ) {
        throw new Error(
          "Attachment was removed"
        );
      }

      if (
        current.status
        === "failed"
      ) {
        throw new Error(
          current.error
          || "File upload failed"
        );
      }

      if (
        current.documentId
        !== null
      ) {
        return (
          current.documentId
        );
      }

      await new Promise(
        (resolve) =>
          window.setTimeout(
            resolve,
            150
          )
      );
    }

    throw new Error(
      "File upload took too long"
    );
  }


  async function runQuestion({
    questionText,
    documentIds = [],
    optimisticDocuments = [],
    outgoingAttachment = null,
    clearQuestion = false,
    allowGeneralKnowledgeOverride,
  }: RunQuestionOptions) {
    if (
      sendingRef.current
    ) {
      return;
    }

    const trimmedQuestion =
      questionText.trim();

    if (
      !trimmedQuestion
      && !outgoingAttachment
    ) {
      return;
    }

    const effectiveAllowGeneralKnowledge =
      allowGeneralKnowledgeOverride
      ?? allowGeneralKnowledge;

    const currentDocuments =
      chat?.documents
      ?? [];

    if (
      !outgoingAttachment
      && documentIds.length === 0
      && currentDocuments.length === 0
      && !effectiveAllowGeneralKnowledge
    ) {
      setComposerError(
        "Files mode needs at least one attached document. Attach a file or switch to General."
      );

      return;
    }

    const isDraft =
      !chat
      || chatId <= 0;

    if (
      isDraft
      && !createPersistedChat
    ) {
      setComposerError(
        "New chat is not ready yet. Please refresh and try again."
      );

      return;
    }

    const token =
      getToken();

    sendingRef.current =
      true;

    stopRequestedRef.current =
      false;

    setSending(true);
    setComposerError(null);

    const temporaryUserId =
      -Date.now();

    const temporaryAssistantId =
      temporaryUserId - 1;

    const temporaryDocumentId =
      temporaryAssistantId - 1;

    const now =
      new Date()
        .toISOString();

    const initialDocuments =
      outgoingAttachment
        ? [
            buildOptimisticDocument(
              outgoingAttachment,
              temporaryDocumentId
            ),
          ]
        : optimisticDocuments;

    const optimisticUserMessage:
      Message = {
        id:
          temporaryUserId,

        chat_id:
          chatId,

        role:
          "user",

        content:
          trimmedQuestion,

        status:
          "processing",

        error:
          null,

        sources:
          null,

        documents:
          initialDocuments,

        created_at:
          now,
      };

    const streamingAssistantMessage:
      Message = {
        id:
          temporaryAssistantId,

        chat_id:
          chatId,

        role:
          "assistant",

        content:
          "",

        status:
          "processing",

        error:
          null,

        sources:
          null,

        documents:
          [],

        created_at:
          now,
      };

    setMessages(
      (current) => [
        ...current,
        optimisticUserMessage,
        streamingAssistantMessage,
      ]
    );

    if (clearQuestion) {
      setQuestion("");
    }

    const controller =
      new AbortController();

    abortControllerRef.current =
      controller;

    let fullAnswer = "";

    let activeChatId =
      chatId;

    let createdChatId:
      number | null = null;

    let streamRequestStarted =
      false;

    try {
      let resolvedDocumentIds = [
        ...documentIds,
      ];

      if (
        outgoingAttachment
      ) {
        const documentId =
          outgoingAttachment
            .documentId
          ?? await waitForDocumentId(
            outgoingAttachment.localId
          );

        resolvedDocumentIds = [
          documentId,
        ];

        setMessages(
          (current) =>
            current.map(
              (message) => {
                if (
                  message.id
                  !== temporaryUserId
                ) {
                  return message;
                }

                return {
                  ...message,

                  documents:
                    message.documents
                      .map(
                        (
                          document
                        ) => ({
                          ...document,

                          id:
                            documentId,

                          processing_status:
                            (
                              attachmentRef
                              .current
                              ?.status
                              ?? document
                                .processing_status
                            ),

                          processing_progress:
                            (
                              attachmentRef
                              .current
                              ?.progress
                              ?? document
                                .processing_progress
                            ),
                        })
                      ),
                };
              }
            )
        );

      }


      if (
        !chat
        || activeChatId <= 0
      ) {
        const newChat =
          await createPersistedChat!();

        activeChatId =
          newChat.id;

        createdChatId =
          newChat.id;

        setMessages(
          (current) =>
            current.map(
              (message) =>
                (
                  message.id
                  === temporaryUserId
                  || message.id
                  === temporaryAssistantId
                )
                  ? {
                      ...message,
                      chat_id:
                        activeChatId,
                    }
                  : message
            )
        );

        if (
          resolvedDocumentIds.length > 0
        ) {
          if (!attachDocumentToChat) {
            throw new Error(
              "Could not attach the document to the new chat"
            );
          }

          for (
            const documentId
            of resolvedDocumentIds
          ) {
            await attachDocumentToChat(
              activeChatId,
              documentId
            );
          }
        }
      }


      streamRequestStarted =
        true;

      const response =
        await fetch(
          `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/chats/${activeChatId}/ask/stream`,
          {
            method:
              "POST",

            credentials:
              "include",

            headers: {
              "Content-Type":
                "application/json",

              ...(token && token !== "__cookie__"
                ? {
                    Authorization:
                      `Bearer ${token}`,
                  }
                : {}),
            },

            body:
              JSON.stringify({
                question:
                  trimmedQuestion
                  || (
                    "Please analyze "
                    + "the attached file."
                  ),

                allow_general_knowledge:
                  effectiveAllowGeneralKnowledge,

                document_ids:
                  resolvedDocumentIds,
              }),

            signal:
              controller.signal,
          }
        );


      if (!response.ok) {
        let errorMessage =
          "Could not generate answer";

        try {
          const errorData =
            await response.json();

          if (
            errorData.detail
          ) {
            errorMessage =
              errorData.detail;
          }

        } catch {
        }

        throw new Error(
          errorMessage
        );
      }


      if (!response.body) {
        throw new Error(
          "Streaming response is not available"
        );
      }

      /*
        Only remove the attachment from the composer after the
        backend has accepted the request and a stream is available.
        If processing failed (empty/corrupt/unreadable file), keep
        the attachment visible so the user can see its failed state
        and remove/replace it deliberately.
      */
      if (outgoingAttachment) {
        clearComposerAttachment();
      }


      const reader =
        response.body
          .getReader();

      const decoder =
        new TextDecoder(
          "utf-8"
        );

      let buffer = "";


      while (true) {
        const {
          done,
          value,
        } = await reader.read();

        if (value) {
          buffer +=
            decoder.decode(
              value,
              {
                stream:
                  !done,
              }
            );
        }

        const lines =
          buffer.split(
            "\n"
          );

        buffer =
          lines.pop()
          ?? "";


        for (
          const rawLine
          of lines
        ) {
          const line =
            rawLine.trim();

          if (!line) {
            continue;
          }

          let streamEvent:
            Record<
              string,
              unknown
            >;

          try {
            streamEvent =
              JSON.parse(
                line
              );

          } catch {
            continue;
          }


          if (
            streamEvent.type
            === "chat_title"
          ) {
            const title =
              typeof streamEvent.title
              === "string"
                ? streamEvent.title.trim()
                : "";

            if (
              title
              && onChatTitleGenerated
            ) {
              await onChatTitleGenerated({
                title,
              });
            }

            continue;
          }


          if (
            streamEvent.type
            === "attachment_status"
          ) {
            const documents =
              Array.isArray(
                streamEvent.documents
              )
                ? streamEvent.documents
                : [];

            setMessages(
              (current) =>
                current.map(
                  (message) => {
                    if (
                      message.id
                      !== temporaryUserId
                    ) {
                      return message;
                    }

                    return {
                      ...message,

                      documents:
                        message.documents
                          .map(
                            (
                              document
                            ) => {
                              const updated =
                                documents
                                  .find(
                                    (
                                      item:
                                        {
                                          id?: number;
                                        }
                                    ) =>
                                      item.id
                                      === document.id
                                  );

                              if (!updated) {
                                return document;
                              }

                              return {
                                ...document,

                                processing_status:
                                  updated
                                    .processing_status
                                  ?? document
                                    .processing_status,

                                processing_stage:
                                  updated
                                    .processing_stage
                                  ?? document
                                    .processing_stage,

                                processing_progress:
                                  updated
                                    .processing_progress
                                  ?? document
                                    .processing_progress,
                              };
                            }
                          ),
                    };
                  }
                )
            );

            continue;
          }


          if (
            streamEvent.type
            === "token"
          ) {
            const content =
              typeof streamEvent.content
              === "string"
                ? streamEvent.content
                : "";

            fullAnswer +=
              content;

            setMessages(
              (current) =>
                current.map(
                  (message) =>
                    message.id
                    === temporaryAssistantId
                      ? {
                          ...message,

                          content:
                            fullAnswer,
                        }
                      : message
                )
            );

            continue;
          }


          if (
            streamEvent.type
            === "summary_generated"
          ) {
            const documentId =
              Number(
                streamEvent
                  .document_id
              );

            const summaryId =
              Number(
                streamEvent
                  .summary_id
              );

            const version =
              Number(
                streamEvent
                  .version
              );

            if (
              Number.isInteger(
                documentId
              )
              && documentId > 0
              && Number.isInteger(
                summaryId
              )
              && summaryId > 0
              && onSummaryGenerated
            ) {
              await onSummaryGenerated({
                documentId,
                summaryId,
                version,
              });
            }

            continue;
          }


          if (
            streamEvent.type
            === "done"
          ) {
            const sources:
              Source[] =
                Array.isArray(
                  streamEvent.sources
                )
                  ? streamEvent.sources
                  : [];

            setMessages(
              (current) =>
                current.map(
                  (message) =>
                    message.id
                    === temporaryAssistantId
                      ? {
                          ...message,

                          content:
                            fullAnswer,

                          status:
                            "completed",

                          sources,
                        }
                      : message
                )
            );

            continue;
          }


          if (
            streamEvent.type
            === "error"
          ) {
            const errorMessage =
              typeof streamEvent.message
              === "string"
                ? streamEvent.message
                : "Could not generate answer";

            throw new Error(
              errorMessage
            );
          }
        }

        if (done) {
          break;
        }
      }


      if (
        createdChatId !== null
      ) {
        router.replace(
          `/chat/${createdChatId}`
        );
      } else {
        await refreshMessages();
      }

      setComposerError(null);

    } catch (error) {
      const wasStopped =
        stopRequestedRef.current
        || isAbortError(
          error
        );

      if (wasStopped) {
        setMessages(
          (current) =>
            current.map(
              (message) => {
                if (
                  message.id
                  === temporaryUserId
                ) {
                  return {
                    ...message,

                    status:
                      "completed",

                    error:
                      null,
                  };
                }

                if (
                  message.id
                  === temporaryAssistantId
                ) {
                  return {
                    ...message,

                    content:
                      fullAnswer
                      || "Generation stopped.",

                    status:
                      "stopped",

                    error:
                      null,
                  };
                }

                return message;
              }
            )
        );

        setComposerError(null);

        if (
          createdChatId !== null
          && streamRequestStarted
        ) {
          router.replace(
            `/chat/${createdChatId}`
          );
        }

        return;
      }

      console.error(
        "[CHAT STREAM ERROR]",
        error
      );

      const rawErrorMessage =
        error instanceof Error
          ? error.message
          : "Could not generate answer";

      const failedAttachment =
        attachmentRef.current;

      const visibleErrorMessage =
        failedAttachment?.status
        === "failed"
          ? (
              failedAttachment.error
              || (
                "The attached file could not be processed. "
                + "It may be empty, corrupted, or contain no readable content."
              )
            )
          : rawErrorMessage
            === "One or more attached documents failed to process"
              ? (
                  "The attached file could not be processed. "
                  + "It may be empty, corrupted, or contain no readable content."
                )
              : rawErrorMessage;

      setMessages(
        (current) =>
          current.filter(
            (message) =>
              message.id
              !== temporaryAssistantId
          )
          .map(
            (message) =>
              message.id
              === temporaryUserId
                ? {
                    ...message,

                    status:
                      "failed",

                    error:
                      visibleErrorMessage,
                  }
                : message
          )
      );

      if (
        createdChatId !== null
        && streamRequestStarted
      ) {
        router.replace(
          `/chat/${createdChatId}`
        );
      } else if (
        createdChatId === null
      ) {
        await refreshMessages();
      }

      setComposerError(
        visibleErrorMessage
      );

    } finally {
      if (
        abortControllerRef.current
        === controller
      ) {
        abortControllerRef.current =
          null;
      }

      sendingRef.current =
        false;

      stopRequestedRef.current =
        false;

      setSending(
        false
      );
    }
  }


  async function sendQuestion(
    event: FormEvent
  ) {
    event.preventDefault();

    if (
      sendingRef.current
    ) {
      return;
    }

    setComposerError(null);

    const outgoingAttachment =
      attachmentRef.current;

    const trimmedQuestion =
      question.trim();

    if (
      !trimmedQuestion
      && !outgoingAttachment
    ) {
      return;
    }

    await runQuestion({
      questionText:
        trimmedQuestion,

      outgoingAttachment,

      clearQuestion:
        true,
    });
  }


  async function sendQuestionText(
    questionText: string,
  ) {
    await runQuestion({
      questionText,
      clearQuestion: true,
      allowGeneralKnowledgeOverride: true,
    });
  }


  async function retryMessage(
    message: Message
  ) {
    if (
      sendingRef.current
      || message.role
      !== "user"
    ) {
      return;
    }

    const retryQuestion =
      message.content.trim();

    if (!retryQuestion) {
      return;
    }

    const retryDocuments =
      (
        message.documents
        ?? []
      ).filter(
        (
          document
        ) =>
          Number.isInteger(
            document.id
          )
          && document.id > 0
      );

    const retryDocumentIds =
      retryDocuments.map(
        (
          document
        ) =>
          document.id
      );

    await runQuestion({
      questionText:
        retryQuestion,

      documentIds:
        retryDocumentIds,

      optimisticDocuments:
        retryDocuments,

      clearQuestion:
        false,
    });
  }


  function stopGeneration() {
    if (
      !sendingRef.current
      || !abortControllerRef.current
    ) {
      return;
    }

    stopRequestedRef.current =
      true;

    abortControllerRef.current
      .abort();
  }


  return {
    question,
    setQuestion,

    sending,

    composerError,
    setComposerError,

    allowGeneralKnowledge,
    setAllowGeneralKnowledge,

    sendQuestion,
    sendQuestionText,
    retryMessage,
    stopGeneration,
  };
}