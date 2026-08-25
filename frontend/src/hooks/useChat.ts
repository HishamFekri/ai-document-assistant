"use client";

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
  }, [router]);


  const refreshChats =
    useCallback(
      async () => {
        const token =
          requireToken();

        if (!token) {
          return;
        }

        try {
          const data =
            await getChats(
              token
            );

          setChats(
            data
          );

        } catch (error) {
          console.error(
            "[CHAT] Could not refresh chats",
            error
          );
        }
      },
      [
        requireToken,
      ]
    );


  const refreshChat =
    useCallback(
      async () => {
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
          const data =
            await getChat(
              token,
              chatId
            );

          setChat(
            data
          );

        } catch (error) {
          console.error(
            "[CHAT] Could not refresh chat",
            error
          );
        }
      },
      [
        validChatId,
        chatId,
        requireToken,
      ]
    );


  const refreshMessages =
    useCallback(
      async () => {
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
          const data =
            await getMessages(
              token,
              chatId
            );

          setMessages(
            data
          );

        } catch (error) {
          console.error(
            "[CHAT] Could not refresh messages",
            error
          );
        }
      },
      [
        validChatId,
        chatId,
        requireToken,
      ]
    );


  const loadAppData =
    useCallback(
      async () => {
        if (
          appLoadedRef.current
        ) {
          return;
        }

        const token =
          requireToken();

        if (!token) {
          setLoading(
            false
          );

          return;
        }

        try {
          setLoading(
            true
          );

          const [
            userData,
            chatsData,
          ] = await Promise.all([
            getCurrentUser(
              token
            ),

            getChats(
              token
            ),
          ]);

          setUser(
            userData
          );

          setChats(
            chatsData
          );

          appLoadedRef.current =
            true;

        } catch (error) {
          console.error(
            "[CHAT] Could not load app data",
            error
          );

        } finally {
          setLoading(
            false
          );
        }
      },
      [
        requireToken,
      ]
    );


  const loadActiveChat =
    useCallback(
      async () => {
        if (
          !validChatId
          || chatId === null
        ) {
          setChatLoading(
            false
          );

          return;
        }

        const token =
          requireToken();

        if (!token) {
          setChatLoading(
            false
          );

          return;
        }

        try {
          setChatLoading(
            true
          );

          const [
            chatData,
            messagesData,
          ] = await Promise.all([
            getChat(
              token,
              chatId
            ),

            getMessages(
              token,
              chatId
            ),
          ]);

          setChat(
            chatData
          );

          setMessages(
            messagesData
          );

        } catch (error) {
          console.error(
            "[CHAT] Could not load active chat",
            error
          );

          setChat(
            null
          );

          setMessages(
            []
          );

        } finally {
          setChatLoading(
            false
          );
        }
      },
      [
        validChatId,
        chatId,
        requireToken,
      ]
    );


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
      async (): Promise<Chat> => {
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
              token
            );

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
          setCreatingChat(
            false
          );
        }
      },
      [
        requireToken,
      ]
    );


  const attachDocumentToChat =
    useCallback(
      async (
        targetChatId: number,
        documentId: number
      ): Promise<Chat> => {
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
            documentId
          );

        setChat(
          updatedChat
        );

        return updatedChat;
      },
      [
        requireToken,
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

            setChat(
              detachedChat
            );

          } catch (error) {
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
      setUploading(
        false
      );
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

      setChat(
        updatedChat
      );

    } catch (error) {
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

      setChat(
        updatedChat
      );

    } catch (error) {
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


  function logout() {
    fetch(
      `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/auth/logout`,
      {
        method: "POST",
        credentials: "include",
      }
    ).catch((error) => {
      console.error("[LOGOUT ERROR]", error);
    });

    appLoadedRef.current =
      false;

    router.replace(
      "/"
    );
  }


  return {
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