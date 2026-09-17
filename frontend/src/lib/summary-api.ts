import { readNDJSON } from "@/lib/ndjson";
import {
  DocumentSummary,
} from "@/types/summary";


export type SummaryMode =
  | "summary"
  | "transcription";


const API_URL =
  process.env.NEXT_PUBLIC_API_URL
  || "http://localhost:8000";


function getHeaders(
  token: string,
  includeJson: boolean = false
) {
  return {
    ...(token !== "__cookie__"
      ? {
          Authorization:
            `Bearer ${token}`,
        }
      : {}),

    ...(includeJson
      ? {
          "Content-Type":
            "application/json",
        }
      : {}),
  };
}


async function parseError(
  response: Response
): Promise<string> {
  if (
    response.status === 401
    && typeof window !== "undefined"
  ) {
    window.dispatchEvent(
      new Event("auth-expired")
    );
  }

  try {
    const data =
      await response.json();

    if (
      typeof data?.detail
      === "string"
    ) {
      return data.detail;
    }

  } catch {
  }

  return "Something went wrong";
}


export async function getSelectedSummary(
  token: string,
  chatId: number,
  documentId: number,
  mode: SummaryMode = "summary",
  signal?: AbortSignal
): Promise<DocumentSummary | null> {
  const params =
    new URLSearchParams({
      chat_id:
        String(chatId),

      mode,
    });

  const response =
    await fetch(
      (
        `${API_URL}`
        + `/documents/${documentId}`
        + `/summaries/selected`
        + `?${params.toString()}`
      ),
      {
        credentials: "include",
        signal,
        headers:
          getHeaders(token),

        cache:
          "no-store",
      }
    );

  if (
    response.status
    === 404
  ) {
    return null;
  }

  if (!response.ok) {
    throw new Error(
      await parseError(
        response
      )
    );
  }

  return response.json();
}


export type SummaryStreamEvent =
  | {
      type: "start";
      summary_id: number;
    }
  | {
      type: "title";
      title: string;
    }
  | {
      type: "section";
      section: NonNullable<
        DocumentSummary["content"]
      >["sections"][number];
    }
  | {
      type: "done";
      summary: DocumentSummary;
    }
  | {
      type: "error";
      message: string;
    };


export async function streamDocumentSummary(
  token: string,
  chatId: number,
  documentId: number,
  onEvent: (
    event: SummaryStreamEvent
  ) => void,
  mode: SummaryMode = "summary",
  signal?: AbortSignal
): Promise<DocumentSummary> {
  const response =
    await fetch(
      (
        `${API_URL}`
        + `/documents/${documentId}`
        + `/summaries/generate/stream`
      ),
      {
        method:
          "POST",

        credentials: "include",

        headers:
          getHeaders(
            token,
            true
          ),

        body:
          JSON.stringify({
            chat_id:
              chatId,

            mode,
          }),

        signal,
      }
    );

  if (!response.ok) {
    throw new Error(
      await parseError(
        response
      )
    );
  }

  if (!response.body) {
    throw new Error(
      "Summary stream is not available"
    );
  }

  let completed: DocumentSummary | null = null;
  await readNDJSON(response.body, (raw) => {
    const event = raw as SummaryStreamEvent;
    if (event.type === "error") throw new Error(event.message || "Could not generate summary");
    if (event.type === "done") {
      const summary = event.summary;
      if (!summary || summary.chat_id !== chatId || summary.document_id !== documentId
          || summary.mode !== mode || !Number.isInteger(summary.id) || summary.id <= 0
          || !["completed", "cancelled", "failed"].includes(summary.status)) {
        throw new Error("Invalid summary completion");
      }
      completed = summary;
    }
    onEvent(event);
  }, signal);
  if (!completed) throw new Error("Summary stream ended unexpectedly");
  return completed;
}


export async function cancelDocumentSummaryGeneration(
  token: string,
  chatId: number,
  documentId: number,
  summaryId: number
) {
  const response =
    await fetch(
      (
        `${API_URL}`
        + `/documents/${documentId}`
        + `/summaries/${summaryId}/cancel`
        + `?chat_id=${chatId}`
      ),
      {
        method:
          "POST",

        credentials: "include",

        headers:
          getHeaders(
            token
          ),
      }
    );

  if (!response.ok) {
    throw new Error(
      await parseError(
        response
      )
    );
  }

  return response.json();
}


export async function deleteDocumentSummary(
  token: string,
  chatId: number,
  documentId: number,
  summaryId: number,
  signal?: AbortSignal
) {
  const response =
    await fetch(
      (
        `${API_URL}`
        + `/documents/${documentId}`
        + `/summaries/${summaryId}`
        + `?chat_id=${chatId}`
      ),
      {
        method:
          "DELETE",

        credentials: "include",
        signal,

        headers:
          getHeaders(
            token
          ),
      }
    );

  if (!response.ok) {
    throw new Error(
      await parseError(
        response
      )
    );
  }

  return response.json();
}