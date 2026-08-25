export type SummaryAssistantMessage = {
  id: number;

  chat_id:
    number | null;

  document_id: number;

  role:
    | "user"
    | "assistant";

  content: string;

  created_at: string;
};


export type SummaryAssistantAction =
  | "update_preferences"
  | "generate_summary";


export type GeneratedSummary = {
  id: number;

  chat_id:
    number | null;

  document_id: number;

  version: number;

  status:
    | "pending"
    | "generating"
    | "completed"
    | "failed";

  content: {
    title: string;

    sections: Array<{
      type:
        | "text"
        | "image"
        | "table"
        | "equation";

      title:
        string | null;

      content:
        string | null;

      asset_id:
        number | null;

      caption:
        string | null;

      location:
        string | null;
    }>;
  } | null;

  is_selected: boolean;

  error:
    string | null;

  created_at: string;
};


export type SummaryAssistantChatResponse = {
  messages:
    SummaryAssistantMessage[];
};


export type SummaryAssistantReplyResponse = {
  user_message:
    SummaryAssistantMessage;

  assistant_message:
    SummaryAssistantMessage;

  action:
    SummaryAssistantAction;

  generated_summary:
    GeneratedSummary | null;
};


const API_URL =
  process.env.NEXT_PUBLIC_API_URL
  || "http://localhost:8000";


function buildAuthHeaders(
  token: string
) {
  return {
    ...(token !== "__cookie__"
      ? {
          Authorization:
            `Bearer ${token}`,
        }
      : {}),
  };
}


function buildJsonHeaders(
  token: string
) {
  return {
    "Content-Type":
      "application/json",

    ...(token !== "__cookie__"
      ? {
          Authorization:
            `Bearer ${token}`,
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

    if (
      typeof data?.message
      === "string"
    ) {
      return data.message;
    }

    return (
      `Request failed (${response.status})`
    );

  } catch {
    return (
      `Request failed (${response.status})`
    );
  }
}


export async function getSummaryAssistantMessages(
  token: string,
  chatId: number,
  documentId: number
): Promise<SummaryAssistantMessage[]> {
  const response =
    await fetch(
      (
        `${API_URL}`
        + `/documents/${documentId}`
        + `/summary-assistant/messages`
        + `?chat_id=${chatId}`
      ),
      {
        method:
          "GET",

        credentials: "include",

        headers:
          buildAuthHeaders(
            token
          ),

        cache:
          "no-store",
      }
    );


  if (!response.ok) {
    throw new Error(
      await parseError(
        response
      )
    );
  }


  const data:
    SummaryAssistantChatResponse =
      await response.json();


  return data.messages;
}


export async function sendSummaryAssistantMessage(
  token: string,
  chatId: number,
  documentId: number,
  content: string
): Promise<SummaryAssistantReplyResponse> {
  const response =
    await fetch(
      (
        `${API_URL}`
        + `/documents/${documentId}`
        + `/summary-assistant/messages`
      ),
      {
        method:
          "POST",

        credentials: "include",

        headers:
          buildJsonHeaders(
            token
          ),

        body:
          JSON.stringify({
            chat_id:
              chatId,

            content,
          }),
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


export async function resetSummaryAssistant(
  token: string,
  chatId: number,
  documentId: number
): Promise<{
  message: string;

  deleted_messages: number;
}> {
  const response =
    await fetch(
      (
        `${API_URL}`
        + `/documents/${documentId}`
        + `/summary-assistant/messages`
        + `?chat_id=${chatId}`
      ),
      {
        method:
          "DELETE",

        credentials: "include",

        headers:
          buildAuthHeaders(
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