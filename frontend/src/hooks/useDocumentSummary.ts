"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  DocumentSummary,
} from "@/types/summary";

import {
  SummaryMode,
  cancelDocumentSummaryGeneration,
  deleteDocumentSummary,
  getSelectedSummary,
  streamDocumentSummary,
} from "@/lib/summary-api";


type Props = {
  token: string | null;

  chatId: number | null;

  documentId: number | null;

  mode: SummaryMode;
};


export function useDocumentSummary({
  token,
  chatId,
  documentId,
  mode,
}: Props) {
  const [
    selectedSummary,
    setSelectedSummary,
  ] = useState<
    DocumentSummary | null
  >(null);

  const [
    loading,
    setLoading,
  ] = useState(false);

  const [
    generating,
    setGenerating,
  ] = useState(false);

  const [
    deletingSummaryId,
    setDeletingSummaryId,
  ] = useState<
    number | null
  >(null);

  const [
    error,
    setError,
  ] = useState<
    string | null
  >(null);


  const abortControllerRef =
    useRef<
      AbortController | null
    >(null);

  const generationRunRef =
    useRef(0);


  const activeSummaryIdRef =
    useRef<number | null>(
      null
    );

  const stopRequestedRef =
    useRef(false);


  useEffect(() => {
    generationRunRef.current += 1;

    const controller =
      abortControllerRef.current;

    abortControllerRef.current =
      null;

    activeSummaryIdRef.current =
      null;

    stopRequestedRef.current =
      false;

    if (
      controller
      && !controller.signal.aborted
    ) {
      controller.abort();
    }

    const resetTimeout = window.setTimeout(() => {
      setGenerating(false);
    }, 0);

    return () => {
      window.clearTimeout(resetTimeout);
      generationRunRef.current += 1;

      const activeController =
        abortControllerRef.current;

      abortControllerRef.current =
        null;

      if (
        activeController
        && !activeController
          .signal
          .aborted
      ) {
        activeController.abort();
      }
    };
  }, [
    token,
    chatId,
    documentId,
    mode,
  ]);


  const refreshSummary =
    useCallback(
      async () => {
        if (
          !token
          || chatId === null
          || documentId === null
        ) {
          setSelectedSummary(
            null
          );

          setError(
            null
          );

          return;
        }

        try {
          setLoading(true);

          setError(null);

          setSelectedSummary(
            null
          );

          const summary =
            await getSelectedSummary(
              token,
              chatId,
              documentId,
              mode
            );

          setSelectedSummary(
            summary
          );

        } catch (error) {
          console.error(
            "[SUMMARY LOAD ERROR]",
            error
          );

          setError(
            error instanceof Error
              ? error.message
              : "Could not load summary"
          );

        } finally {
          setLoading(false);
        }
      },
      [
        token,
        chatId,
        documentId,
        mode,
      ]
    );


  useEffect(() => {
    const refreshTimeout = window.setTimeout(() => {
      void refreshSummary();
    }, 0);

    return () => window.clearTimeout(refreshTimeout);
  }, [
    refreshSummary,
  ]);


  const generateSummary =
    useCallback(
      async (
        requestedMode: SummaryMode = mode
      ) => {
        if (
          !token
          || chatId === null
          || documentId === null
        ) {
          return null;
        }

        const previousController =
          abortControllerRef.current;

        if (
          previousController
          && !previousController
            .signal
            .aborted
        ) {
          previousController.abort();
        }

        const controller =
          new AbortController();

        abortControllerRef.current =
          controller;

        activeSummaryIdRef.current =
          null;

        stopRequestedRef.current =
          false;

        const runId =
          generationRunRef.current
          + 1;

        generationRunRef.current =
          runId;

        const isCurrentRun = () =>
          generationRunRef.current
            === runId
          && abortControllerRef.current
            === controller
          && !controller.signal.aborted;

        try {
          setGenerating(
            true
          );

          setError(
            null
          );

          let streamingId =
            selectedSummary?.id
            ?? -Date.now();

          let streamingTitle =
            requestedMode
            === "transcription"
              ? "Generating transcription..."
              : "Generating summary...";

          let streamingSections:
            NonNullable<
              DocumentSummary["content"]
            >["sections"] = [];

          if (
            isCurrentRun()
          ) {
            setSelectedSummary(
              {
                id:
                  streamingId,

                chat_id:
                  chatId,

                document_id:
                  documentId,

                mode:
                  requestedMode,

                version:
                  1,

                status:
                  "generating",

                content: {
                  title:
                    streamingTitle,

                  sections:
                    [],
                },

                is_selected:
                  true,

                error:
                  null,

                created_at:
                  selectedSummary
                    ?.created_at
                  ?? new Date()
                    .toISOString(),
              }
            );
          }

          const completed =
            await streamDocumentSummary(
              token,
              chatId,
              documentId,
              (event) => {
                if (
                  event.type
                  === "start"
                ) {
                  streamingId =
                    event.summary_id;

                  activeSummaryIdRef.current =
                    event.summary_id;

                  if (
                    stopRequestedRef.current
                  ) {
                    void (
                      cancelDocumentSummaryGeneration(
                        token,
                        chatId,
                        documentId,
                        event.summary_id
                      )
                      .catch(
                        (error) => {
                          console.error(
                            "[SUMMARY CANCEL ERROR]",
                            error
                          );
                        }
                      )
                      .finally(
                        () => {
                          if (
                            !controller
                              .signal
                              .aborted
                          ) {
                            controller.abort();
                          }

                          if (
                            abortControllerRef
                              .current
                            === controller
                          ) {
                            abortControllerRef
                              .current =
                                null;
                          }

                          activeSummaryIdRef
                            .current =
                              null;
                        }
                      )
                    );

                    return;
                  }

                  if (
                    !isCurrentRun()
                  ) {
                    return;
                  }

                  setSelectedSummary(
                    (current) =>
                      current
                      && current.mode
                        === requestedMode
                        ? {
                            ...current,

                            id:
                              streamingId,
                          }
                        : current
                  );

                  return;
                }

                if (
                  !isCurrentRun()
                ) {
                  return;
                }

                if (
                  event.type
                  === "title"
                ) {
                  streamingTitle =
                    event.title;

                  setSelectedSummary(
                    (current) =>
                      current
                      && current.mode
                        === requestedMode
                        ? {
                            ...current,

                            content: {
                              title:
                                streamingTitle,

                              sections:
                                streamingSections,
                            },
                          }
                        : current
                  );

                  return;
                }

                if (
                  event.type
                  === "section"
                ) {
                  streamingSections = [
                    ...streamingSections,
                    event.section,
                  ];

                  setSelectedSummary(
                    (current) =>
                      current
                      && current.mode
                        === requestedMode
                        ? {
                            ...current,

                            content: {
                              title:
                                streamingTitle,

                              sections:
                                streamingSections,
                            },
                          }
                        : current
                  );

                  return;
                }

                if (
                  event.type
                  === "done"
                  && event.summary.mode
                    === requestedMode
                ) {
                  setSelectedSummary(
                    event.summary
                  );
                }
              },
              requestedMode,
              controller.signal
            );

          if (
            !isCurrentRun()
          ) {
            return null;
          }

          if (
            completed.mode
            !== requestedMode
          ) {
            throw new Error(
              "Generated document mode does not match the active view"
            );
          }

          activeSummaryIdRef.current =
            null;

          setSelectedSummary(
            completed
          );

          return completed;

        } catch (error) {
          const aborted =
            controller.signal.aborted
            || (
              error instanceof Error
              && error.name
                === "AbortError"
            );

          if (aborted) {
            return null;
          }

          if (
            generationRunRef.current
            !== runId
          ) {
            return null;
          }

          console.error(
            "[SUMMARY GENERATE ERROR]",
            error
          );

          setError(
            error instanceof Error
              ? error.message
              : "Could not generate summary"
          );

          await refreshSummary();

          return null;

        } finally {
          if (
            generationRunRef.current
              === runId
            && abortControllerRef.current
              === controller
          ) {
            abortControllerRef.current =
              null;

            setGenerating(
              false
            );
          }
        }
      },
      [
        token,
        chatId,
        documentId,
        mode,
        selectedSummary,
        refreshSummary,
      ]
    );


  const regenerateSummary =
    useCallback(
      async (
        requestedMode: SummaryMode = mode
      ) => {
        return generateSummary(
          requestedMode
        );
      },
      [
        generateSummary,
        mode,
      ]
    );


  const stopGeneration =
    useCallback(
      () => {
        stopRequestedRef.current =
          true;

        generationRunRef.current += 1;

        const controller =
          abortControllerRef.current;

        const summaryId =
          activeSummaryIdRef.current;

        /*
          Keep exactly what has already streamed on screen.

          Do NOT refresh from the backend here.
          Refreshing used to replace the partial result with the
          previous completed Summary/Transcription, which made Stop
          feel like a reload.
        */
        setSelectedSummary(
          (current) => {
            if (!current) {
              return current;
            }

            const stoppedSection:
              NonNullable<
                DocumentSummary["content"]
              >["sections"][number] = {
                type:
                  "text",

                title:
                  "Generation stopped",

                content:
                  (
                    "Stopped here. "
                    + "To see the complete "
                    + (
                      current.mode
                      === "transcription"
                        ? "transcription"
                        : "summary"
                    )
                    + ", press Regenerate."
                  ),

                asset_id:
                  null,

                caption:
                  null,

                location:
                  null,
              };

            const currentContent =
              current.content
              ?? {
                title:
                  current.mode
                  === "transcription"
                    ? "Transcription"
                    : "Summary",

                sections:
                  [],
              };

            return {
              ...current,

              status:
                "cancelled",

              error:
                null,

              content: {
                ...currentContent,

                sections: [
                  ...currentContent
                    .sections,
                  stoppedSection,
                ],
              },
            };
          }
        );

        /*
          Return the UI to the normal Regenerate state immediately.
          This also re-enables the Summary Assistant input.
        */
        setGenerating(
          false
        );

        setLoading(
          false
        );

        setError(
          null
        );

        /*
          If we already know the real server summary_id, send the
          cancel request in parallel and close the local stream
          immediately.

          The cancel request has its own fetch and is not tied to the
          stream AbortController.
        */
        if (
          summaryId
          && summaryId > 0
          && token
          && chatId !== null
          && documentId !== null
        ) {
          void (
            cancelDocumentSummaryGeneration(
              token,
              chatId,
              documentId,
              summaryId
            )
            .catch(
              (error) => {
                console.error(
                  "[SUMMARY CANCEL ERROR]",
                  error
                );
              }
            )
          );

          activeSummaryIdRef.current =
            null;

          abortControllerRef.current =
            null;

          if (
            controller
            && !controller
              .signal
              .aborted
          ) {
            controller.abort();
          }

          return;
        }

        /*
          If Stop was pressed before the backend sent the "start"
          event, keep this stream alive only long enough to receive
          the real summary_id.

          The stream callback above will cancel that server record
          and then abort this controller. Because generationRunRef
          was already changed, no more streamed sections can update
          the stopped UI.
        */
      },
      [
        token,
        chatId,
        documentId,
      ]
    );


  const removeSummary =
    useCallback(
      async () => {
        if (
          !token
          || chatId === null
          || documentId === null
          || !selectedSummary
          || selectedSummary.id < 1
        ) {
          return false;
        }

        try {
          setDeletingSummaryId(
            selectedSummary.id
          );

          setError(null);

          await deleteDocumentSummary(
            token,
            chatId,
            documentId,
            selectedSummary.id
          );

          setSelectedSummary(
            null
          );

          return true;

        } catch (error) {
          console.error(
            "[SUMMARY DELETE ERROR]",
            error
          );

          setError(
            error instanceof Error
              ? error.message
              : "Could not delete summary"
          );

          return false;

        } finally {
          setDeletingSummaryId(
            null
          );
        }
      },
      [
        token,
        chatId,
        documentId,
        selectedSummary,
      ]
    );


  const addGeneratedSummary =
    useCallback(
      (
        summary: DocumentSummary
      ) => {
        setSelectedSummary(
          summary
        );
      },
      []
    );


  function clearError() {
    setError(null);
  }


  return {
    selectedSummary,

    loading,
    generating,
    deletingSummaryId,
    error,

    refreshSummary,

    generateSummary,
    regenerateSummary,
    stopGeneration,

    addGeneratedSummary,
    removeSummary,

    clearError,
  };
}