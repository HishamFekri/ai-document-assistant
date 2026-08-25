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
  mode: SummaryMode = "summary"
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

  const reader =
    response.body.getReader();

  const decoder =
    new TextDecoder(
      "utf-8"
    );

  let buffer = "";

  let completedSummary:
    DocumentSummary | null = null;


  function processLine(
    rawLine: string
  ) {
    const line =
      rawLine.trim();

    if (!line) {
      return;
    }

    let event:
      SummaryStreamEvent;

    try {
      event =
        JSON.parse(
          line
        );
    } catch {
      return;
    }

    if (
      event.type
      === "error"
    ) {
      throw new Error(
        event.message
        || "Could not generate summary"
      );
    }

    onEvent(
      event
    );

    if (
      event.type
      === "done"
    ) {
      completedSummary =
        event.summary;
    }
  }


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
      processLine(
        rawLine
      );
    }

    if (done) {
      break;
    }
  }


  if (
    buffer.trim()
  ) {
    processLine(
      buffer
    );
  }


  if (
    !completedSummary
  ) {
    throw new Error(
      "Summary stream ended unexpectedly"
    );
  }

  return completedSummary;
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
  summaryId: number
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