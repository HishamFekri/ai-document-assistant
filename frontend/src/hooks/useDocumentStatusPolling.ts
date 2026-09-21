"use client";

import { useEffect } from "react";
import { getDocument } from "@/lib/chat-api";
import { Document } from "@/types/chat";


const POLL_INTERVAL_MS = 2000;


type DocumentStatusPollingOptions = {
  documentIds: number[];
  enabled?: boolean;
  token: string;
  onDocuments: (documents: Document[]) => void;
};


function isTerminal(document: Document) {
  return document.processing_status === "ready"
    || document.processing_status === "failed";
}


export function useDocumentStatusPolling({
  documentIds,
  enabled = true,
  token,
  onDocuments,
}: DocumentStatusPollingOptions) {
  const documentIdsKey = enabled
    ? [...new Set(documentIds)]
        .filter((id) => Number.isInteger(id) && id > 0)
        .sort((left, right) => left - right)
        .join(",")
    : "";

  useEffect(() => {
    if (!documentIdsKey) {
      return;
    }

    const ids = documentIdsKey
      .split(",")
      .map(Number);
    const controller = new AbortController();
    let active = true;
    let timeoutId: number | undefined;

    const schedule = () => {
      timeoutId = window.setTimeout(
        poll,
        POLL_INTERVAL_MS
      );
    };

    const poll = async () => {
      const results = await Promise.allSettled(
        ids.map((documentId) =>
          getDocument(
            token,
            documentId,
            controller.signal
          )
        )
      );

      if (!active) {
        return;
      }

      const documents = results.flatMap((result) =>
        result.status === "fulfilled"
          ? [result.value]
          : []
      );

      if (documents.length > 0) {
        onDocuments(documents);
      }

      const documentsById = new Map(
        documents.map((document) => [document.id, document])
      );
      const shouldContinue = ids.some((documentId) => {
        const document = documentsById.get(documentId);
        return !document || !isTerminal(document);
      });

      if (shouldContinue) {
        schedule();
      }
    };

    schedule();

    return () => {
      active = false;
      controller.abort();
      if (timeoutId !== undefined) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [documentIdsKey, onDocuments, token]);
}
