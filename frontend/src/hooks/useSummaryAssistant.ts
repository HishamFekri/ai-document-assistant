"use client";

import {
  useCallback,
  useEffect,
  useState,
} from "react";

import {
  GeneratedSummary,
  SummaryAssistantMessage,
  getSummaryAssistantMessages,
  resetSummaryAssistant,
  sendSummaryAssistantMessage,
} from "@/lib/summary-assistant-api";


type Props = {
  token: string | null;

  chatId: number | null;

  documentId: number | null;

  onSummaryGenerated?: (
    summary: GeneratedSummary
  ) => void;
};


export function useSummaryAssistant({
  token,
  chatId,
  documentId,
  onSummaryGenerated,
}: Props) {
  const [
    messages,
    setMessages,
  ] = useState<
    SummaryAssistantMessage[]
  >([]);

  const [
    input,
    setInput,
  ] = useState("");

  const [
    loading,
    setLoading,
  ] = useState(false);

  const [
    sending,
    setSending,
  ] = useState(false);

  const [
    resetting,
    setResetting,
  ] = useState(false);

  const [
    error,
    setError,
  ] = useState<
    string | null
  >(null);


  const loadMessages =
    useCallback(
      async () => {
        if (
          !token
          || chatId === null
          || documentId === null
        ) {
          setMessages([]);

          return;
        }

        try {
          setLoading(true);

          setError(null);

          const result =
            await getSummaryAssistantMessages(
              token,
              chatId,
              documentId
            );

          setMessages(
            result
          );

        } catch (error) {
          console.error(
            "[SUMMARY ASSISTANT LOAD ERROR]",
            error
          );

          setError(
            error instanceof Error
              ? error.message
              : "Could not load summary instructions"
          );

        } finally {
          setLoading(false);
        }
      },
      [
        token,
        chatId,
        documentId,
      ]
    );


  useEffect(() => {
    const resetTimeout = window.setTimeout(() => {
      setInput("");
      setError(null);
      setMessages([]);
      void loadMessages();
    }, 0);

    return () => window.clearTimeout(resetTimeout);
  }, [
    loadMessages,
  ]);


  const sendMessage =
    useCallback(
      async (
        customContent?: string
      ) => {
        if (
          !token
          || chatId === null
          || documentId === null
          || sending
        ) {
          return null;
        }

        const content =
          (
            customContent
            ?? input
          ).trim();

        if (!content) {
          return null;
        }

        try {
          setSending(true);

          setError(null);

          const result =
            await sendSummaryAssistantMessage(
              token,
              chatId,
              documentId,
              content
            );

          setMessages(
            (current) => [
              ...current,
              result.user_message,
              result.assistant_message,
            ]
          );

          setInput("");

          if (
            result.generated_summary
          ) {
            onSummaryGenerated?.(
              result.generated_summary
            );
          }

          return result;

        } catch (error) {
          console.error(
            "[SUMMARY ASSISTANT SEND ERROR]",
            error
          );

          setError(
            error instanceof Error
              ? error.message
              : "Could not save summary instruction"
          );

          return null;

        } finally {
          setSending(false);
        }
      },
      [
        token,
        chatId,
        documentId,
        input,
        sending,
        onSummaryGenerated,
      ]
    );


  const resetAssistant =
    useCallback(
      async () => {
        if (
          !token
          || chatId === null
          || documentId === null
          || resetting
        ) {
          return false;
        }

        try {
          setResetting(true);

          setError(null);

          await resetSummaryAssistant(
            token,
            chatId,
            documentId
          );

          setMessages([]);

          setInput("");

          return true;

        } catch (error) {
          console.error(
            "[SUMMARY ASSISTANT RESET ERROR]",
            error
          );

          setError(
            error instanceof Error
              ? error.message
              : "Could not reset summary instructions"
          );

          return false;

        } finally {
          setResetting(false);
        }
      },
      [
        token,
        chatId,
        documentId,
        resetting,
      ]
    );


  function clearError() {
    setError(null);
  }


  return {
    messages,

    input,
    setInput,

    loading,
    sending,
    resetting,

    error,

    loadMessages,
    sendMessage,
    resetAssistant,
    clearError,
  };
}