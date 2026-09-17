"use client";
import { useRequestScope } from "@/hooks/useRequestScope";
import { mergePageItems } from "@/lib/pagination";
import { logoutSession } from "@/lib/logout";

import {
  ChangeEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  useRouter,
} from "next/navigation";

import {
  archiveChat,
  attachDocument,
  createChat,
  deleteChat,
  getChat,
  getChats,
  getCurrentUser,
  getMessages,
  pinChat,
  removeDocument,
  updateChatTitle,
  uploadDocument,
} from "@/lib/chat-api";

import {
  Chat,
  ChatListItem,
  Message,
  User,
} from "@/types/chat";


export type ComposerAttachment = {
  localId: string;
  filename: string;

  status:
    | "uploading"
    | "processing"
    | "ready"
    | "failed";

  progress?: number;

  documentId:
    number | null;

  error:
    string | null;
};


export function useChat(
  chatId: number | null
) {
  const router =
    useRouter();


  const [
    user,
    setUser,
  ] = useState<User | null>(
    null
  );


  const [
    chat,
    setChat,
  ] = useState<Chat | null>(
    null
  );


  const [
    chats,
    setChats,
  ] = useState<ChatListItem[]>(
    []
  );


  const [
    messages,
    setMessages,
  ] = useState<Message[]>(
    []
  );


  const [
    loading,
    setLoading,
  ] = useState(true);


  const [
    chatLoading,
    setChatLoading,
  ] = useState(true);


  const [
    uploading,
    setUploading,
  ] = useState(false);


  const [
    attachment,
    setAttachment,
  ] = useState<
    ComposerAttachment | null
  >(null);


  const cancelledAttachmentIdsRef =
    useRef<Set<string>>(
      new Set()
    );


  const [
    creatingChat,
    setCreatingChat,
  ] = useState(false);


  const appLoadedRef =
    useRef(false);


  const validChatId =
    chatId !== null
    && Number.isInteger(
      chatId
    )
    && chatId > 0;


  const [chatCursor, setChatCursor] = useState<string | null>(null);
  const [messageCursor, setMessageCursor] = useState<string | null>(null);
  const [loadingMoreChats, setLoadingMoreChats] = useState(false);
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false);
  const olderMessagesLoaded = useRef(false);
  const messageScope = useRef<number | null>(chatId);
  const chatPageBusy = useRef(false);
  const messagePageBusy = useRef(false);

  const scope = useRequestScope(String(chatId));
  const reconcileIds = useRef(new Set<number>());

  const getToken =
    useCallback(() => {
      return "__cookie__";
    }, []);


  const requireToken =
    useCallback(() => {
      const token =
        getToken();

      return token;
    }, [
      getToken,
      router,
    ]);


  useEffect(() => {
    const handleAuthExpired = () => {
      scope.dispose();
      setUser(null);
      setChat(null);
      setMessages([]);
      router.replace("/");
    };

    window.addEventListener(
      "auth-expired",
      handleAuthExpired
    );

    return () => {
      window.removeEventListener(
        "auth-expired",
        handleAuthExpired
      );
    };
  }, [router, scope]);


  const refreshChats = useCallback(async () => {
    if (!scope.isActive()) return;
    scope.cancel("chatPage"); chatPageBusy.current = false; setLoadingMoreChats(false);
    const request = scope.begin("chats");
    try {
      const data = await getChats(requireToken(), undefined, request.signal);
      if (!request.current()) return;
      setChats(data.items); setChatCursor(data.nextCursor);
    } catch (error) {
      if (request.current()) console.error("[CHAT] Could not refresh chats", error);
    } finally { request.finish(); }
  }, [scope, requireToken]);

  const refreshChat = useCallback(async () => {
    if (!scope.isActive() || !validChatId || chatId === null) return;
    const request = scope.begin("chat");
    try {
      const data = await getChat(requireToken(), chatId, request.signal);
      if (request.current()) setChat(data);
    } catch (error) {
      if (request.current()) console.error("[CHAT] Could not refresh chat", error);
    } finally { request.finish(); }
  }, [scope, validChatId, chatId, requireToken]);

  const refreshMessages = useCallback(async (replaceTemporaryIds: number[] = []) => {
    if (!scope.isActive() || !validChatId || chatId === null) return;
    for (const id of replaceTemporaryIds) reconcileIds.current.add(id);
    const request = scope.begin("messages");
    try {
      const data = await getMessages(requireToken(), chatId, undefined, request.signal);
      if (!request.current()) return;
      const removed = new Set(reconcileIds.current);
      setMessages(current => mergePageItems(current.filter(message => !removed.has(message.id)
        && !(message.id < 0 && ["completed", "stopped"].includes(message.status ?? ""))), data.items));
      reconcileIds.current.clear();
      if (!olderMessagesLoaded.current) setMessageCursor(data.nextCursor);
    } catch (error) {
      if (request.current()) console.error("[CHAT] Could not refresh messages", error);
    } finally { request.finish(); }
  }, [scope, validChatId, chatId, requireToken]);

  const loadAppData = useCallback(async () => {
    if (!scope.isActive() || appLoadedRef.current) return;
    const request = scope.begin("user");
    setLoading(true);
    try {
      const [userData] = await Promise.all([
        getCurrentUser(requireToken(), request.signal), refreshChats(),
      ]);
      if (!request.current()) return;
      setUser(userData); appLoadedRef.current = true;
    } catch (error) {
      if (request.current()) console.error("[CHAT] Could not load app data", error);
    } finally {
      if (request.current()) { setLoading(false); request.finish(); }
    }
  }, [scope, requireToken, refreshChats]);

  const loadActiveChat = useCallback(async () => {
    if (!scope.isActive()) return;
    messageScope.current = chatId;
    olderMessagesLoaded.current = false;
    messagePageBusy.current = false;
    chatPageBusy.current = false;
    reconcileIds.current.clear();
    setMessageCursor(null); setMessages([]); setChat(null);
    setLoadingOlderMessages(false); setLoadingMoreChats(false);
    if (!validChatId || chatId === null) { setChatLoading(false); return; }
    setChatLoading(true);
    try { await Promise.all([refreshChat(), refreshMessages()]); }
    finally { if (scope.isActive()) setChatLoading(false); }
  }, [scope, validChatId, chatId, refreshChat, refreshMessages]);

  useEffect(() => {
    loadAppData();
  }, [
    loadAppData,
  ]);


  useEffect(() => {
    loadActiveChat();
  }, [
    loadActiveChat,
  ]);


  useEffect(() => {
    if (
      !chat
      || !validChatId
    ) {
      return;
    }

    const hasProcessing =
      chat.documents.some(
        (
          document
        ) =>
          document.processing_status
          === "processing"
      );

    if (!hasProcessing) {
      return;
    }

    const interval =
      window.setInterval(
        () => {
          refreshChat();
        },
        2000
      );

    return () => {
      window.clearInterval(
        interval
      );
    };
  }, [
    chat,
    validChatId,
    refreshChat,
  ]);


  useEffect(() => {
    if (
      !attachment
      || attachment.documentId
      === null
      || !chat
    ) {
      return;
    }

    const document =
      chat.documents.find(
        (item) =>
          item.id
          === attachment.documentId
      );

    if (!document) {
      return;
    }

    const nextStatus:
      ComposerAttachment["status"] =
        document.processing_status
        === "ready"
          ? "ready"
          : document.processing_status
            === "failed"
            ? "failed"
            : "processing";

    setAttachment(
      (current) => {
        if (
          !current
          || current.documentId
          !== document.id
        ) {
          return current;
        }

        if (
          current.status
          === nextStatus
          && current.progress
          === (
            document.processing_progress
            ?? undefined
          )
          && current.error
          === (
            document.processing_error
            ?? null
          )
        ) {
          return current;
        }

        return {
          ...current,
          status:
            nextStatus,
          progress:
            document.processing_progress
            ?? undefined,
          error:
            document.processing_error
            ?? null,
        };
      }
    );
  }, [
    chat,
    attachment?.documentId,
  ]);


  useEffect(() => {
    setAttachment(
      null
    );

    cancelledAttachmentIdsRef
      .current
      .clear();
  }, [
    chatId,
  ]);


  useEffect(() => {
    if (
      !chat
      || !validChatId
      || chatId === null
    ) {
      return;
    }

    const hasPersistedUserMessage =
      messages.some(
        (message) =>
          message.role === "user"
          && message.id > 0
      );

    if (!hasPersistedUserMessage) {
      return;
    }

    setChats(
      (current) => {
        const alreadyListed =
          current.some(
            (item) =>
              item.id === chat.id
          );

        if (alreadyListed) {
          return current;
        }

        return [
          {
            id:
              chat.id,

            title:
              chat.title,

            is_pinned:
              chat.is_pinned,

            is_archived:
              chat.is_archived,

            created_at:
              chat.created_at,
          },

          ...current,
        ];
      }
    );
  }, [
    chat,
    chatId,
    validChatId,
    messages,
  ]);


  const createPersistedChat =
    useCallback(
      async (signal?: AbortSignal): Promise<Chat> => {
        if (!scope.isActive() || signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
        const request = scope.begin("create");
        const stopped = () => { if (request.current()) setCreatingChat(false); };
        signal?.addEventListener("abort", stopped, { once: true });
        const token =
          requireToken();

        if (!token) {
          throw new Error(
            "Your session has expired"
          );
        }

        setCreatingChat(
          true
        );

        try {
          const newChat =
            await createChat(
              token, undefined, signal ?? request.signal
            );
          if (!request.current() || signal?.aborted) throw new DOMException("Request cancelled", "AbortError");

          /*
            Do not add the chat to Recent here.

            The chat becomes visible in Recent only after
            the first persisted user message exists.
          */

          setChat(
            newChat
          );

          return newChat;

        } finally {
          signal?.removeEventListener("abort", stopped);
          if (request.current()) { setCreatingChat(false); request.finish(); }
        }
      },
      [
        requireToken, scope,
      ]
    );


  const attachDocumentToChat =
    useCallback(
      async (
        targetChatId: number,
        documentId: number,
        signal?: AbortSignal
      ): Promise<Chat> => {
        if (!scope.isActive() || signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
        const token =
          requireToken();

        if (!token) {
          throw new Error(
            "Your session has expired"
          );
        }

        const updatedChat =
          await attachDocument(
            token,
            targetChatId,
            documentId, signal
          );
        if (!scope.isActive() || signal?.aborted) throw new DOMException("Request cancelled", "AbortError");

        setChat(
          updatedChat
        );

        return updatedChat;
      },
      [
        requireToken, scope,
      ]
    );


  async function handleCreateChat() {
    if (creatingChat) {
      return;
    }

    /*
      New Chat is a local draft.

      Nothing is written to the database here.
      The real chat is created by the first send.
    */

    setChat(
      null
    );

    setMessages(
      []
    );

    setAttachment(
      null
    );

    cancelledAttachmentIdsRef
      .current
      .clear();

    router.push(
      "/chat"
    );
  }


  async function handleUpload(
    event:
      ChangeEvent<HTMLInputElement>
  ) {
    const file =
      event.target
        .files?.[0];

    event.target.value = "";

    if (!file) {
      return;
    }

    if (attachment) {
      return;
    }

    const token =
      requireToken();

    if (!token) {
      return;
    }

    const localId =
      `${Date.now()}-${file.name}`;

    cancelledAttachmentIdsRef
      .current
      .delete(
        localId
      );

    setAttachment({
      localId,
      filename:
        file.name,
      status:
        "uploading",
      progress:
        undefined,
      documentId:
        null,
      error:
        null,
    });

    setUploading(
      true
    );

    try {
      const document =
        await uploadDocument(
          token,
          file
        );
        if (!scope.isActive()) return;

      const wasCancelled =
        cancelledAttachmentIdsRef
          .current
          .has(
            localId
          );

      let updatedChat:
        Chat | null = null;

      if (
        validChatId
        && chatId !== null
      ) {
        updatedChat =
          await attachDocument(
            token,
            chatId,
            document.id
          );
        if (!scope.isActive()) return;
      }

      if (wasCancelled) {
        if (
          updatedChat
          && chatId !== null
        ) {
          try {
            const detachedChat =
              await removeDocument(
                token,
                chatId,
                document.id
              );
        if (!scope.isActive()) return;

            setChat(
              detachedChat
            );

          } catch (error) {
      if (!scope.isActive()) return;
            console.error(
              "[CHAT] Could not detach cancelled upload",
              error
            );
          }
        }

        cancelledAttachmentIdsRef
          .current
          .delete(
            localId
          );

        return;
      }

      if (updatedChat) {
        setChat(
          updatedChat
        );
      }

      setAttachment(
        (current) => {
          if (
            !current
            || current.localId
            !== localId
          ) {
            return current;
          }

          return {
            ...current,

            documentId:
              document.id,

            status:
              document.processing_status
              === "ready"
                ? "ready"
                : document.processing_status
                  === "failed"
                  ? "failed"
                  : "processing",

            progress:
              document.processing_progress
              ?? undefined,

            error:
              document.processing_error
              ?? null,
          };
        }
      );

    } catch (error) {
      if (!scope.isActive()) return;
      console.error(
        "[CHAT UPLOAD ERROR]",
        error
      );

      setAttachment(
        (current) => {
          if (
            !current
            || current.localId
            !== localId
          ) {
            return current;
          }

          return {
            ...current,
            status:
              "failed",
            error:
              error instanceof Error
                ? error.message
                : "Could not upload document",
          };
        }
      );

    } finally {
      if (scope.isActive()) setUploading(false);
    }
  }


  async function handleRemoveAttachment() {
    const currentAttachment =
      attachment;

    if (!currentAttachment) {
      return;
    }

    setAttachment(
      null
    );

    if (
      currentAttachment.documentId
      === null
    ) {
      cancelledAttachmentIdsRef
        .current
        .add(
          currentAttachment.localId
        );

      return;
    }

    if (
      !validChatId
      || chatId === null
    ) {
      return;
    }

    const token =
      requireToken();

    if (!token) {
      return;
    }

    try {
      const updatedChat =
        await removeDocument(
          token,
          chatId,
          currentAttachment.documentId
        );
        if (!scope.isActive()) return;

      setChat(
        updatedChat
      );

    } catch (error) {
      if (!scope.isActive()) return;
      console.error(
        "[CHAT] Could not remove composer attachment",
        error
      );
    }
  }


  function clearComposerAttachment() {
    setAttachment(
      null
    );
  }

  async function handleRemoveDocument(
    documentId: number
  ) {
    if (
      !validChatId
      || chatId === null
    ) {
      return;
    }

    const token =
      requireToken();

    if (!token) {
      return;
    }

    try {
      const updatedChat =
        await removeDocument(
          token,
          chatId,
          documentId
        );
        if (!scope.isActive()) return;

      setChat(
        updatedChat
      );

    } catch (error) {
      if (!scope.isActive()) return;
      alert(
        error instanceof Error
          ? error.message
          : "Could not remove document"
      );
    }
  }


  async function handleRenameChat(
    targetChatId: number,
    title: string
  ) {
    const token =
      requireToken();

    if (!token) {
      return;
    }

    const trimmedTitle =
      title.trim();

    if (!trimmedTitle) {
      return;
    }

    try {
      const updatedChat =
        await updateChatTitle(
          token,
          targetChatId,
          trimmedTitle
        );
      if (!scope.isActive()) return;

      setChats(
        (current) =>
          current.map(
            (item) =>
              item.id ===
              targetChatId
                ? {
                    ...item,

                    title:
                      updatedChat.title,
                  }
                : item
          )
      );

      if (
        targetChatId ===
        chatId
      ) {
        setChat(
          updatedChat
        );
      }

    } catch (error) {
      if (!scope.isActive()) return;
      alert(
        error instanceof Error
          ? error.message
          : "Could not rename chat"
      );

      throw error;
    }
  }


  async function handlePinChat(
    targetChatId: number
  ) {
    const token =
      requireToken();

    if (!token) {
      return;
    }

    try {
      const updatedChat =
        await pinChat(
          token,
          targetChatId
        );
        if (!scope.isActive()) return;

      setChats(
        (current) =>
          current.map(
            (item) =>
              item.id ===
              targetChatId
                ? {
                    ...item,

                    is_pinned:
                      updatedChat.is_pinned,

                    is_archived:
                      updatedChat.is_archived,
                  }
                : item
          )
      );

      if (
        targetChatId ===
        chatId
      ) {
        setChat(
          updatedChat
        );
      }

    } catch (error) {
      if (!scope.isActive()) return;
      alert(
        error instanceof Error
          ? error.message
          : "Could not update pin status"
      );

      throw error;
    }
  }


  async function handleArchiveChat(
    targetChatId: number
  ) {
    const token =
      requireToken();

    if (!token) {
      return;
    }

    try {
      const updatedChat =
        await archiveChat(
          token,
          targetChatId
        );
        if (!scope.isActive()) return;

      const updatedChats =
        chats.map(
          (item) =>
            item.id ===
            targetChatId
              ? {
                  ...item,

                  is_pinned:
                    updatedChat.is_pinned,

                  is_archived:
                    updatedChat.is_archived,
                }
              : item
        );

      setChats(
        updatedChats
      );

      if (
        targetChatId !==
        chatId
      ) {
        return;
      }

      setChat(
        updatedChat
      );

      if (
        !updatedChat.is_archived
      ) {
        return;
      }

      const nextChat =
        updatedChats.find(
          (item) =>
            item.id !==
              targetChatId
            && !item.is_archived
        );

      if (nextChat) {
        router.push(
          `/chat/${nextChat.id}`
        );

        return;
      }

      setChat(
        null
      );

      setMessages(
        []
      );

      setAttachment(
        null
      );

      router.push(
        "/chat"
      );

    } catch (error) {
      if (!scope.isActive()) return;
      alert(
        error instanceof Error
          ? error.message
          : "Could not update archive status"
      );

      throw error;
    }
  }


  async function handleDeleteChat(
    targetChatId: number,
    options?: {
      suppressAlert?: boolean;
    }
  ) {
    const token =
      requireToken();

    if (!token) {
      return;
    }

    try {
      await deleteChat(
        token,
        targetChatId
      );
        if (!scope.isActive()) return;

      const remainingChats =
        chats.filter(
          (item) =>
            item.id !==
            targetChatId
        );

      setChats(
        remainingChats
      );

      if (
        targetChatId !==
        chatId
      ) {
        return;
      }

      const nextChat =
        remainingChats.find(
          (item) =>
            !item.is_archived
        );

      if (nextChat) {
        router.push(
          `/chat/${nextChat.id}`
        );

        return;
      }

      setChat(
        null
      );

      setMessages(
        []
      );

      setAttachment(
        null
      );

      router.push(
        "/chat"
      );

    } catch (error) {
      if (!scope.isActive()) return;
      const message =
        error instanceof Error
          && error.message
            ? error.message
            : "Could not delete chat";

      console.error(
        "[CHAT] Could not delete chat",
        error
      );

      if (
        !options?.suppressAlert
      ) {
        alert(
          message
        );
      }

      throw new Error(
        message
      );
    }
  }


  async function loadMoreChats() {
    if (!scope.isActive() || !chatCursor || chatPageBusy.current) return;
    const request = scope.begin("chatPage");
    chatPageBusy.current = true;
    setLoadingMoreChats(true);
    try {
      const page = await getChats(requireToken(), chatCursor, request.signal);
      if (!request.current()) return;
      setChats((current) => mergePageItems(current, page.items, "chats"));
      setChatCursor(page.nextCursor);
    } catch (error) {
      if (!request.current()) return;
      window.alert(error instanceof Error ? error.message : "Could not load more chats");
    } finally {
      if (request.current()) { chatPageBusy.current = false; setLoadingMoreChats(false); request.finish(); }
    }
  }

  async function loadOlderMessages() {
    if (!scope.isActive() || !messageCursor || chatId === null || messagePageBusy.current) return;
    const request = scope.begin("messagePage");
    const targetChatId = chatId;
    messagePageBusy.current = true;
    setLoadingOlderMessages(true);
    try {
      const page = await getMessages(requireToken(), targetChatId, messageCursor, request.signal);
      if (!request.current()) return;
      if (messageScope.current !== targetChatId) return;
      olderMessagesLoaded.current = true;
      setMessages((current) => mergePageItems(current, page.items));
      setMessageCursor(page.nextCursor);
    } catch (error) {
      if (!request.current()) return;
      window.alert(error instanceof Error ? error.message : "Could not load older messages");
    } finally {
      if (request.current()) { messagePageBusy.current = false; setLoadingOlderMessages(false); request.finish(); }
    }
  }

  async function logout() {
    await logoutSession(() => {
      scope.dispose();
      appLoadedRef.current = false;
      setUser(null);
      setChat(null);
      setChats([]);
      setMessages([]);
      setAttachment(null);
      router.replace("/");
    }, (message) => window.alert(message));
  }


  return {
    hasMoreChats: chatCursor !== null,
    hasOlderMessages: messageCursor !== null,
    loadingMoreChats, loadingOlderMessages, loadMoreChats, loadOlderMessages,
    user,
    chat,
    chats,
    messages,

    setChat,
    setChats,
    setMessages,

    loading,
    chatLoading,

    uploading,
    attachment,
    creatingChat,

    getToken,

    refreshChat,
    refreshChats,
    refreshMessages,

    createPersistedChat,
    attachDocumentToChat,

    handleCreateChat,
    handleUpload,
    handleRemoveAttachment,
    clearComposerAttachment,
    handleRemoveDocument,

    handleRenameChat,
    handlePinChat,
    handleArchiveChat,
    handleDeleteChat,

    logout,
  };
}
