import { Page, readPage } from "@/lib/pagination";
import {
  Chat,
  Document,
  ChatListItem,
  Message,
  User,
} from "@/types/chat";
import { parseUploadPolicy, uploadError, validateUpload } from "@/lib/upload-policy";


const API_URL =
  process.env.NEXT_PUBLIC_API_URL
  ?? "http://localhost:8000";


function buildAuthHeaders(
  token: string
): Record<string, string> {
  return token && token !== "__cookie__"
    ? {
        Authorization: `Bearer ${token}`,
      }
    : {};
}


function buildJsonHeaders(
  token: string
) {
  return {
    "Content-Type": "application/json",
    ...buildAuthHeaders(token),
  };
}


function formatDetail(
  detail: unknown,
  fallback: string
): string {
  if (
    typeof detail ===
    "string"
  ) {
    return detail;
  }


  if (
    Array.isArray(detail)
  ) {
    return detail
      .map(
        (item) => {
          if (
            typeof item ===
            "string"
          ) {
            return item;
          }


          if (
            item
            && typeof item ===
              "object"
          ) {
            const record =
              item as Record<
                string,
                unknown
              >;


            if (
              typeof record.msg ===
              "string"
            ) {
              return record.msg;
            }


            try {
              return JSON.stringify(
                record
              );
            } catch {
              return fallback;
            }
          }


          return String(
            item
          );
        }
      )
      .join(" | ");
  }


  if (
    detail
    && typeof detail ===
      "object"
  ) {
    try {
      return JSON.stringify(
        detail
      );
    } catch {
      return fallback;
    }
  }


  return fallback;
}


async function readError(
  response: Response,
  fallback: string
): Promise<string> {
  try {
    const data =
      await response.json();


    const detail =
      data?.detail
      ?? data?.message;


    return formatDetail(
      detail,
      `${fallback} (${response.status})`
    );

  } catch {
    try {
      const text =
        await response.text();


      if (text) {
        return (
          `${fallback} (${response.status}): ${text}`
        );
      }

    } catch {
    }


    return (
      `${fallback} (${response.status})`
    );
  }
}


async function ensureOk(
  response: Response,
  fallback: string
) {
  if (response.ok) {
    return;
  }

  if (
    response.status === 401
    && typeof window !== "undefined"
  ) {
    window.dispatchEvent(
      new Event("auth-expired")
    );
  }


  const message =
    await readError(
      response,
      fallback
    );


  console.error(
    `[API ERROR] ${response.status}`,
    response.url,
    message
  );


  throw new Error(
    message
  );
}


export async function getCurrentUser(
  token: string,
  signal?: AbortSignal
): Promise<User> {
  const response =
    await fetch(
      `${API_URL}/auth/me`,
      {
        credentials: "include",
        signal,
        headers:
          buildAuthHeaders(
            token
          ),
      }
    );


  await ensureOk(
    response,
    "Could not load user"
  );


  return response.json();
}


export async function getChat(
  token: string,
  chatId: number,
  signal?: AbortSignal
): Promise<Chat> {
  const response =
    await fetch(
      `${API_URL}/chats/${chatId}`,
      {
        credentials: "include",
        signal,
        headers:
          buildAuthHeaders(
            token
          ),
      }
    );


  await ensureOk(
    response,
    "Could not load chat"
  );


  return response.json();
}


export async function getDocuments(token: string, cursor?: string): Promise<Page<Document>> {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor) params.set("cursor", cursor);
  const response = await fetch(`${API_URL}/documents?${params}`, {
    credentials: "include", headers: buildAuthHeaders(token),
  });
  await ensureOk(response, "Could not load documents");
  return readPage<Document>(response);
}

export async function getChats(
  token: string,
  cursor?: string,
  signal?: AbortSignal
): Promise<Page<ChatListItem>> {
  const response =
    await fetch(
      `${API_URL}/chats` + `?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      {
        credentials: "include",
        signal,
        headers:
          buildAuthHeaders(
            token
          ),
      }
    );


  await ensureOk(
    response,
    "Could not load chats"
  );


  return readPage<ChatListItem>(response);
}


export async function getMessages(
  token: string,
  chatId: number,
  cursor?: string,
  signal?: AbortSignal
): Promise<Page<Message>> {
  const response =
    await fetch(
      `${API_URL}/chats/${chatId}/messages` + `?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      {
        credentials: "include",
        signal,
        headers:
          buildAuthHeaders(
            token
          ),
      }
    );


  await ensureOk(
    response,
    "Could not load messages"
  );


  return readPage<Message>(response);
}


export async function createChat(
  token: string,
  title = "New chat",
  signal?: AbortSignal
): Promise<Chat> {
  const response =
    await fetch(
      `${API_URL}/chats`,
      {
        method: "POST",

        credentials: "include",
        signal,

        headers:
          buildJsonHeaders(
            token
          ),

        body:
          JSON.stringify({
            title,
            document_ids: [],
          }),
      }
    );


  await ensureOk(
    response,
    "Could not create chat"
  );


  return response.json();
}


export async function updateChatTitle(
  token: string,
  chatId: number,
  title: string
): Promise<Chat> {
  const response =
    await fetch(
      `${API_URL}/chats/${chatId}`,
      {
        method: "PATCH",

        credentials: "include",

        headers:
          buildJsonHeaders(
            token
          ),

        body:
          JSON.stringify({
            title,
          }),
      }
    );


  await ensureOk(
    response,
    "Could not rename chat"
  );


  return response.json();
}


export async function deleteChat(
  token: string,
  chatId: number
) {
  const response =
    await fetch(
      `${API_URL}/chats/${chatId}`,
      {
        method: "DELETE",

        credentials: "include",

        headers:
          buildAuthHeaders(
            token
          ),
      }
    );


  await ensureOk(
    response,
    "Could not delete chat"
  );


  return response.json();
}


export async function pinChat(
  token: string,
  chatId: number
): Promise<Chat> {
  const response =
    await fetch(
      `${API_URL}/chats/${chatId}/pin`,
      {
        method: "PATCH",

        credentials: "include",

        headers:
          buildAuthHeaders(
            token
          ),
      }
    );


  await ensureOk(
    response,
    "Could not update pin status"
  );


  return response.json();
}


export async function archiveChat(
  token: string,
  chatId: number
): Promise<Chat> {
  const response =
    await fetch(
      `${API_URL}/chats/${chatId}/archive`,
      {
        method: "PATCH",

        credentials: "include",

        headers:
          buildAuthHeaders(
            token
          ),
      }
    );


  await ensureOk(
    response,
    "Could not update archive status"
  );


  return response.json();
}


export async function getUploadPolicy(token: string) {
  try {
    const response = await fetch(`${API_URL}/documents/upload-policy`, {
      credentials: "include",
      headers: buildAuthHeaders(token),
      cache: "no-store",
    });
    if (!response.ok) {
      if (response.status === 401 && typeof window !== "undefined") {
        window.dispatchEvent(new Event("auth-expired"));
      }
      throw new Error("Policy unavailable");
    }
    return parseUploadPolicy(await response.json());
  } catch {
    throw new Error("Upload limits are unavailable. Please try again shortly.");
  }
}

export async function uploadDocument(
  token: string,
  file: File
) {
  const policy = await getUploadPolicy(token);
  const validationError = validateUpload(file, policy);
  if (validationError) throw new Error(validationError);
  const formData =
    new FormData();


  formData.append(
    "file",
    file
  );


  const response =
    await fetch(
      `${API_URL}/documents`,
      {
        method: "POST",

        credentials: "include",

        headers:
          buildAuthHeaders(
            token
          ),

        body:
          formData,
      }
    );


  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("auth-expired"));
    }
    throw new Error(await uploadError(response));
  }


  return response.json();
}


export async function attachDocument(
  token: string,
  chatId: number,
  documentId: number,
  signal?: AbortSignal
): Promise<Chat> {
  const response =
    await fetch(
      `${API_URL}/chats/${chatId}/documents/${documentId}`,
      {
        method: "POST",

        credentials: "include",
        signal,

        headers:
          buildAuthHeaders(
            token
          ),
      }
    );


  await ensureOk(
    response,
    "Could not attach document"
  );


  return response.json();
}


export async function removeDocument(
  token: string,
  chatId: number,
  documentId: number
): Promise<Chat> {
  const response =
    await fetch(
      `${API_URL}/chats/${chatId}/documents/${documentId}`,
      {
        method: "DELETE",

        credentials: "include",

        headers:
          buildAuthHeaders(
            token
          ),
      }
    );


  await ensureOk(
    response,
    "Could not remove document"
  );


  return response.json();
}
