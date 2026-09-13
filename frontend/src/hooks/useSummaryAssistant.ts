"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRequestScope } from "@/hooks/useRequestScope";
import { GeneratedSummary, SummaryAssistantMessage, getSummaryAssistantMessages,
  resetSummaryAssistant, sendSummaryAssistantMessage } from "@/lib/summary-assistant-api";

type Props = { token: string | null; chatId: number | null; documentId: number | null;
  onSummaryGenerated?: (summary: GeneratedSummary) => void };

export function useSummaryAssistant({ token, chatId, documentId, onSummaryGenerated }: Props) {
  const scope = useRequestScope(JSON.stringify([token, chatId, documentId]));
  const [messages, setMessages] = useState<SummaryAssistantMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);

  const loadMessages = useCallback(async () => {
    if (!scope.isActive() || busy.current || !token || chatId === null || documentId === null) return;
    const request = scope.begin("read");
    setLoading(true); setError(null);
    try {
      const result = await getSummaryAssistantMessages(token, chatId, documentId, request.signal);
      if (request.current()) setMessages(result);
    } catch (failure) {
      if (request.current()) setError(failure instanceof Error ? failure.message : "Could not load summary instructions");
    } finally {
      if (request.current()) { setLoading(false); request.finish(); }
    }
  }, [scope, token, chatId, documentId]);

  useEffect(() => {
    busy.current = false;
    const timer = window.setTimeout(() => {
      if (busy.current) return;
      setInput(""); setError(null); setMessages([]); setSending(false); setResetting(false); setLoading(false);
      void loadMessages();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadMessages]);

  const sendMessage = useCallback(async (customContent?: string) => {
    if (!scope.isActive() || !token || chatId === null || documentId === null || busy.current) return null;
    const content = (customContent ?? input).trim();
    if (!content) return null;
    busy.current = true;
    scope.cancel("read");
    const request = scope.begin("write");
    setSending(true); setLoading(false); setError(null);
    try {
      const result = await sendSummaryAssistantMessage(token, chatId, documentId, content, request.signal);
      if (!request.current()) return null;
      setMessages(current => [...new Map([...current, result.user_message, result.assistant_message].map(m => [m.id, m])).values()]);
      setInput("");
      if (result.generated_summary) onSummaryGenerated?.(result.generated_summary);
      return result;
    } catch (failure) {
      if (request.current()) setError(failure instanceof Error ? failure.message : "Could not save summary instruction");
      return null;
    } finally {
      if (request.current()) { busy.current = false; setSending(false); request.finish(); }
    }
  }, [scope, token, chatId, documentId, input, onSummaryGenerated]);

  const resetAssistant = useCallback(async () => {
    if (!scope.isActive() || !token || chatId === null || documentId === null || busy.current) return false;
    busy.current = true;
    scope.cancel("read");
    const request = scope.begin("write");
    setResetting(true); setLoading(false); setError(null);
    try {
      await resetSummaryAssistant(token, chatId, documentId, request.signal);
      if (!request.current()) return false;
      setMessages([]); setInput(""); return true;
    } catch (failure) {
      if (request.current()) setError(failure instanceof Error ? failure.message : "Could not reset summary instructions");
      return false;
    } finally {
      if (request.current()) { busy.current = false; setResetting(false); request.finish(); }
    }
  }, [scope, token, chatId, documentId]);

  const cancelPending = useCallback(() => {
    scope.cancel("read"); scope.cancel("write"); busy.current = false;
    setLoading(false); setSending(false); setResetting(false);
  }, [scope]);

  return { messages, input, setInput, loading, sending, resetting, error, loadMessages,
    sendMessage, resetAssistant, cancelPending, clearError: () => setError(null) };
}
