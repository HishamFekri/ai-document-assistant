"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRequestScope } from "@/hooks/useRequestScope";
import { DocumentSummary } from "@/types/summary";
import { SummaryMode, cancelDocumentSummaryGeneration, deleteDocumentSummary,
  getSelectedSummary, streamDocumentSummary } from "@/lib/summary-api";

type Props = { token: string | null; chatId: number | null; documentId: number | null; mode: SummaryMode };

export function useDocumentSummary({ token, chatId, documentId, mode }: Props) {
  const scope = useRequestScope(JSON.stringify([token, chatId, documentId, mode]));
  const [selectedSummary, setSelectedSummary] = useState<DocumentSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [deletingSummaryId, setDeletingSummaryId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const generationActive = useRef(false);

  const refreshSummary = useCallback(async () => {
    if (!scope.isActive() || generationActive.current) return;
    const request = scope.begin("read");
    if (!token || chatId === null || documentId === null) {
      setSelectedSummary(null); setLoading(false); request.finish(); return;
    }
    setLoading(true); setError(null);
    try {
      const summary = await getSelectedSummary(token, chatId, documentId, mode, request.signal);
      if (request.current()) setSelectedSummary(summary);
    } catch (failure) {
      if (request.current()) setError(failure instanceof Error ? failure.message : "Could not load summary");
    } finally {
      if (request.current()) { setLoading(false); request.finish(); }
    }
  }, [scope, token, chatId, documentId, mode]);

  useEffect(() => {
    generationActive.current = false;
    const timer = window.setTimeout(() => {
      if (generationActive.current) return;
      setSelectedSummary(null); setGenerating(false); setDeletingSummaryId(null); setError(null);
      void refreshSummary();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshSummary]);

  const generateSummary = useCallback(async (requestedMode: SummaryMode = mode) => {
    if (!scope.isActive() || !token || chatId === null || documentId === null || requestedMode !== mode) return null;
    scope.cancel("read"); scope.cancel("write");
    const request = scope.begin("generation");
    generationActive.current = true;
    let summaryId: number | null = null;
    let terminal = false;
    // Capture this run's IDs. An old cancellation must never cancel a newer summary.
    request.signal.addEventListener("abort", () => {
      if (summaryId !== null && !terminal) {
        void cancelDocumentSummaryGeneration(token, chatId, documentId, summaryId).catch(() => {});
      }
    }, { once: true });
    setGenerating(true); setLoading(false); setDeletingSummaryId(null); setError(null);
    setSelectedSummary({ id: -Date.now(), chat_id: chatId, document_id: documentId, mode,
      version: 1, status: "generating", content: { title: mode === "transcription" ? "Generating transcription..." : "Generating summary...", sections: [] },
      is_selected: true, error: null, created_at: new Date().toISOString() });
    try {
      const completed = await streamDocumentSummary(token, chatId, documentId, (event) => {
        if (!request.current()) return;
        if (event.type === "start") {
          summaryId = event.summary_id;
          setSelectedSummary(current => current ? { ...current, id: event.summary_id } : current);
        } else if (event.type === "title") {
          setSelectedSummary(current => current ? { ...current, content: { title: event.title, sections: current.content?.sections ?? [] } } : current);
        } else if (event.type === "section") {
          setSelectedSummary(current => current ? { ...current, content: { title: current.content?.title ?? "", sections: [...(current.content?.sections ?? []), event.section] } } : current);
        } else if (event.type === "done") {
          terminal = true;
        }
      }, mode, request.signal);
      if (!request.current()) return null;
      setSelectedSummary(completed);
      if (completed.status === "failed") setError(completed.error || "Could not generate summary");
      return completed;
    } catch (failure) {
      if (request.current()) {
        const message = failure instanceof Error ? failure.message : "Could not generate summary";
        setError(message);
        setSelectedSummary(current => current ? { ...current, status: "failed", error: message } : current);
      }
      return null;
    } finally {
      if (request.current()) {
        generationActive.current = false; setGenerating(false); request.finish();
      }
    }
  }, [scope, token, chatId, documentId, mode]);

  const stopGeneration = useCallback(() => {
    if (!scope.isActive() || !generationActive.current) return;
    generationActive.current = false;
    scope.cancel("generation"); scope.cancel("read");
    setGenerating(false); setLoading(false); setError(null);
    setSelectedSummary(current => current ? { ...current, status: "cancelled", error: null } : current);
    // Abort immediately even before start. Server disconnect cleanup owns any
    // record whose ID has not reached this client; never wait indefinitely for it.
  }, [scope]);

  const removeSummary = useCallback(async () => {
    if (!scope.isActive() || !token || chatId === null || documentId === null || !selectedSummary || selectedSummary.id < 1 || generationActive.current) return false;
    scope.cancel("read");
    const request = scope.begin("write");
    setDeletingSummaryId(selectedSummary.id); setError(null);
    try {
      await deleteDocumentSummary(token, chatId, documentId, selectedSummary.id, request.signal);
      if (!request.current()) return false;
      setSelectedSummary(null); return true;
    } catch (failure) {
      if (request.current()) setError(failure instanceof Error ? failure.message : "Could not delete summary");
      return false;
    } finally {
      if (request.current()) { setDeletingSummaryId(null); request.finish(); }
    }
  }, [scope, token, chatId, documentId, selectedSummary]);

  const addGeneratedSummary = useCallback((summary: DocumentSummary) => {
    if (!scope.isActive() || summary.chat_id !== chatId || summary.document_id !== documentId || summary.mode !== mode) return;
    scope.cancel("read"); setLoading(false); setSelectedSummary(summary);
  }, [scope, chatId, documentId, mode]);

  return { selectedSummary, loading, generating, deletingSummaryId, error, refreshSummary,
    generateSummary, regenerateSummary: generateSummary, stopGeneration, addGeneratedSummary,
    removeSummary, clearError: () => setError(null) };
}
