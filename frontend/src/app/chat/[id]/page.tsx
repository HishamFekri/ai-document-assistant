"use client";

import {
  useEffect,
  useRef,
  useState,
} from "react";

import {
  useParams,
  useRouter,
} from "next/navigation";

import {
  ArrowDown,
  Loader2,
  Menu,
  Sparkles,
} from "lucide-react";

import ChatSidebar from "@/components/chat/ChatSidebar";
import ChatMessage from "@/components/chat/ChatMessage";
import ChatComposer from "@/components/chat/ChatComposer";
import UploadedDocsPanel from "@/components/chat/UploadedDocsPanel";
import ChatHeaderActions from "@/components/chat/ChatHeaderActions";

import DocumentSummaryPanel from "@/components/documents/DocumentSummaryPanel";

import {
  Document,
  Chat,
} from "@/types/chat";

import {
  useChat,
} from "@/hooks/useChat";

import {
  useChatStream,
} from "@/hooks/useChatStream";

type ActiveView =
  | "chat"
  | "summary"
  | "transcription";

type DocumentView =
  | "summary"
  | "transcription";


export default function ChatPage({
  draft = false,
}: {
  draft?: boolean;
}) {
  const params =
    useParams();

  const router =
    useRouter();


  const rawChatId =
    draft ? undefined : params?.id;


  let chatId:
    number | null = null;


  if (
    typeof rawChatId ===
    "string"
  ) {
    const parsed =
      Number(
        rawChatId
      );

    if (
      Number.isInteger(
        parsed
      )
      && parsed > 0
    ) {
      chatId = parsed;
    }
  }


  if (
    Array.isArray(
      rawChatId
    )
    && rawChatId.length > 0
  ) {
    const parsed =
      Number(
        rawChatId[0]
      );

    if (
      Number.isInteger(
        parsed
      )
      && parsed > 0
    ) {
      chatId = parsed;
    }
  }


  const fileInputRef =
    useRef<HTMLInputElement | null>(
      null
    );

  const messagesEndRef =
    useRef<HTMLDivElement | null>(
      null
    );

  const messagesContainerRef =
    useRef<HTMLElement | null>(
      null
    );

  const [
    showScrollToBottom,
    setShowScrollToBottom,
  ] = useState(false);

  const [
    chatDeleteError,
    setChatDeleteError,
  ] = useState<string | null>(null);


  const [
    activeView,
    setActiveView,
  ] = useState<ActiveView>(
    "chat"
  );


  const [
    filesOpen,
    setFilesOpen,
  ] = useState(false);


  const [
    mobileSidebarOpen,
    setMobileSidebarOpen,
  ] = useState(false);


  const [
    selectedDocument,
    setSelectedDocument,
  ] = useState<
    Document | null
  >(null);


  const [
    summaryToken,
    setSummaryToken,
  ] = useState<
    string | null
  >(null);


  const {
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
  } = useChat(
    chatId
  );

  const workspaceChat: Chat | null =
    chat
    ?? (draft
      ? {
          id: 0,
          title: "New chat",
          is_pinned: false,
          is_archived: false,
          created_at: new Date().toISOString(),
          documents: [],
        }
      : null);


  async function openDocumentView(
    document: Document,
    view: DocumentView
  ) {
    const token =
      getToken();

    if (!token) {
      console.error(
        "[DOCUMENT VIEW] No auth token"
      );

      return;
    }


    setSelectedDocument(document);
    setSummaryToken(token);
    setFilesOpen(false);
    setActiveView(view);
  }


  async function openDocumentSummary(
    document: Document
  ) {
    await openDocumentView(
      document,
      "summary"
    );
  }


  async function openDocumentTranscription(
    document: Document
  ) {
    await openDocumentView(
      document,
      "transcription"
    );
  }


  async function openDocumentModeView(
    view: DocumentView
  ) {
    const currentSelectedDocument =
      selectedDocument
        ? chat?.documents.find(
            (document) =>
              document.id
              === selectedDocument.id
          )
        : null;


    if (
      currentSelectedDocument
      && summaryToken
    ) {
      setSelectedDocument(
        currentSelectedDocument
      );

      setFilesOpen(
        false
      );

      setActiveView(
        view
      );

      return;
    }


    const firstDocument =
      chat?.documents.find(
        (document) =>
          document.processing_status
          === "ready"
      )
      ?? chat?.documents[0];


    if (!firstDocument) {
      setActiveView(
        view
      );

      setFilesOpen(
        false
      );

      return;
    }


    await openDocumentView(
      firstDocument,
      view
    );
  }


  async function openSummaryView() {
    await openDocumentModeView(
      "summary"
    );
  }


  async function openTranscriptionView() {
    await openDocumentModeView(
      "transcription"
    );
  }


  function openChatView() {
    setActiveView(
      "chat"
    );
  }


  useEffect(() => {
    if (
      activeView === "chat"
      || selectedDocument
      || !chat
      || chat.documents.length === 0
    ) {
      return;
    }

    const document =
      chat.documents.find(
        (item) =>
          item.processing_status
          === "ready"
      )
      ?? chat.documents[0];

    const token =
      getToken();

    if (!token) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setSelectedDocument(document);
      setSummaryToken(token);
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, [
    activeView,
    selectedDocument,
    chat,
    getToken,
  ]);


  const {
    question,
    setQuestion,

    sending,

    composerError,
    setComposerError,

    allowGeneralKnowledge,
    setAllowGeneralKnowledge,

    sendQuestion,
    sendQuestionText,
  } = useChatStream({
    chatId:
      chatId ?? 0,

    chat,

    messages,

    setMessages,

    refreshMessages,

    getToken,

    createPersistedChat,

    attachDocumentToChat,

    attachment,

    clearComposerAttachment,

    onSummaryGenerated:
      async ({
        documentId,
      }) => {
        const document =
          chat?.documents.find(
            (
              item
            ) =>
              item.id
              === documentId
          );

        if (!document) {
          return;
        }

        await openDocumentSummary(
          document
        );
      },

    onChatTitleGenerated:
      ({
        title,
      }) => {
        const nextTitle =
          title.trim();

        if (!nextTitle) {
          return;
        }

        setChat(
          (current) =>
            current
            && current.id === chatId
              ? {
                  ...current,
                  title: nextTitle,
                }
              : current
        );

        setChats(
          (current) =>
            current.map(
              (item) =>
                item.id === chatId
                  ? {
                      ...item,
                      title: nextTitle,
                    }
                  : item
            )
        );
      },
  });


  useEffect(() => {
    if (
      chatLoading
      || activeView
      !== "chat"
    ) {
      return;
    }

    messagesEndRef
      .current
      ?.scrollIntoView({
        behavior:
          "smooth",
      });

  }, [
    messages,
    chatLoading,
    activeView,
  ]);


  useEffect(() => {
    if (
      activeView !== "chat"
    ) {
      setShowScrollToBottom(
        false
      );

      return;
    }

    const container =
      messagesContainerRef.current;

    if (!container) {
      return;
    }

    const updateScrollButton = () => {
      const hasScrollableContent =
        container.scrollHeight
        > container.clientHeight + 8;

      const distanceFromBottom =
        container.scrollHeight
        - container.scrollTop
        - container.clientHeight;

      const nearBottom =
        distanceFromBottom <= 24;

      setShowScrollToBottom(
        hasScrollableContent
        && !nearBottom
      );
    };

    updateScrollButton();

    container.addEventListener(
      "scroll",
      updateScrollButton,
      { passive: true }
    );

    return () => {
      container.removeEventListener(
        "scroll",
        updateScrollButton
      );
    };
  }, [
    chatLoading,
    activeView,
    chatId,
  ]);


  useEffect(() => {
    if (
      activeView !== "chat"
    ) {
      return;
    }

    const frameId =
      window.requestAnimationFrame(
        () => {
          const container =
            messagesContainerRef.current;

          if (!container) {
            return;
          }

          const hasScrollableContent =
            container.scrollHeight
            > container.clientHeight + 8;

          const distanceFromBottom =
            container.scrollHeight
            - container.scrollTop
            - container.clientHeight;

          const nearBottom =
            distanceFromBottom <= 24;

          setShowScrollToBottom(
            hasScrollableContent
            && !nearBottom
          );
        }
      );

    return () => {
      window.cancelAnimationFrame(
        frameId
      );
    };
  }, [
    messages,
    chatLoading,
    activeView,
    chatId,
  ]);


  function scrollToBottom() {
    const container =
      messagesContainerRef.current;

    if (!container) {
      return;
    }

    setShowScrollToBottom(
      false
    );

    container.scrollTo({
      top:
        container.scrollHeight,
      behavior:
        "smooth",
    });
  }


  useEffect(() => {
    if (
      rawChatId !== undefined
      && chatId === null
    ) {
      router.replace(
        "/chat"
      );
    }

  }, [
    rawChatId,
    chatId,
    router,
  ]);


  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setActiveView("chat");
      setSelectedDocument(null);
      setSummaryToken(null);
      setFilesOpen(false);
      setMobileSidebarOpen(false);
    }, 0);

    return () => window.clearTimeout(timeoutId);

  }, [
    chatId,
  ]);


  if (
    chatId === null
    && !draft
  ) {
    return (
      <main
        className="
          flex
          min-h-screen
          items-center
          justify-center
          bg-[var(--background)]
          text-[var(--foreground)]
          transition-colors
          duration-200
        "
      >
        <div
          className="
            flex
            items-center
            gap-2
            text-sm
            text-[var(--text-secondary)]
          "
        >
          <Loader2
            size={17}
            className="
              animate-spin
            "
          />

          Opening chat...
        </div>
      </main>
    );
  }


  if (
    loading
    && !user
  ) {
    return (
      <main
        className="
          flex
          min-h-screen
          items-center
          justify-center
          bg-[var(--background)]
          text-[var(--foreground)]
          transition-colors
          duration-200
        "
      >
        <div
          className="
            flex
            items-center
            gap-2
            text-sm
            text-[var(--text-secondary)]
          "
        >
          <Loader2
            size={17}
            className="
              animate-spin
              text-[var(--primary)]
            "
          />

          Loading...
        </div>
      </main>
    );
  }


  const documentsProcessing =
    chat?.documents.some(
      (
        document
      ) =>
        document.processing_status
        === "processing"
    ) ?? false;


  function renameCurrentChat() {
    if (
      !chat
      || chatId === null
    ) {
      return;
    }


    const currentTitle =
      chat.title
      || "New chat";


    const nextTitle =
      window.prompt(
        "Rename chat",
        currentTitle
      );


    if (!nextTitle) {
      return;
    }


    handleRenameChat(
      chatId,
      nextTitle
    );
  }


  async function deleteCurrentChat() {
    if (
      chatId === null
    ) {
      return;
    }


    const confirmed =
      window.confirm(
        "Delete this chat?"
      );


    if (!confirmed) {
      return;
    }

    setChatDeleteError(null);

    try {
      await handleDeleteChat(chatId);
    } catch (error) {
      setChatDeleteError(
        error instanceof Error
          ? error.message
          : "Could not delete chat. Please try again."
      );
    }
  }


  return (
    <main
      className="
        flex
        h-[100dvh]
        overflow-hidden
        bg-[var(--background)]
        text-[var(--foreground)]
        transition-colors
        duration-200
      "
    >
      <div className="hidden md:block">
        <ChatSidebar
        user={
          user
        }

        chats={
          chats
        }

        activeChatId={
          chatId ?? 0
        }

        creatingChat={
          creatingChat
        }

        onCreateChat={
          handleCreateChat
        }

        onOpenChat={(
          id
        ) => {
          if (
            id === chatId
          ) {
            return;
          }

          router.push(
            `/chat/${id}`
          );
        }}

        onRenameChat={
          handleRenameChat
        }

        onPinChat={
          handlePinChat
        }

        onArchiveChat={
          handleArchiveChat
        }

        onDeleteChat={
          handleDeleteChat
        }

        onLogout={
          logout
        }
        />
      </div>


      {mobileSidebarOpen && (
        <div
          className="
            fixed
            inset-0
            z-[80]
            md:hidden
          "
        >
          <button
            type="button"
            aria-label="Close sidebar"
            onClick={() =>
              setMobileSidebarOpen(false)
            }
            className="
              absolute
              inset-0
              bg-black/55
              backdrop-blur-[1px]
            "
          />

          <div
            className="
              relative
              z-10
              h-full
              w-fit
              shadow-2xl
            "
          >
          <ChatSidebar
            mobile
            onMobileClose={() =>
              setMobileSidebarOpen(false)
            }
        user={
          user
        }

        chats={
          chats
        }

        activeChatId={
          chatId ?? 0
        }

        creatingChat={
          creatingChat
        }

        onCreateChat={
          handleCreateChat
        }

        onOpenChat={(
          id
        ) => {
          if (
            id === chatId
          ) {
            return;
          }

          router.push(
            `/chat/${id}`
          );
        }}

        onRenameChat={
          handleRenameChat
        }

        onPinChat={
          handlePinChat
        }

        onArchiveChat={
          handleArchiveChat
        }

        onDeleteChat={
          handleDeleteChat
        }

        onLogout={
          logout
        }
          />
          </div>
        </div>
      )}

      {chatDeleteError && (
        <div
          role="alert"
          className="absolute left-1/2 top-4 z-50 -translate-x-1/2 rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-600 shadow-lg"
        >
          {chatDeleteError}
        </div>
      )}


      <section
        className="
          relative
          flex
          min-w-0
          flex-1
          bg-[var(--background)]
          transition-colors
          duration-200
        "
      >
        <div
          className="
            flex
            min-w-0
            flex-1
            flex-col
          "
        >
          <header
            className="
              relative
              flex
              min-h-14
              shrink-0
              items-center
              border-b
              border-transparent
              px-2
              sm:px-4
              md:px-6
            "
          >
            <button
              type="button"
              aria-label="Open sidebar"
              onClick={() =>
                setMobileSidebarOpen(true)
              }
              className="
                mr-1
                inline-flex
                h-10
                w-10
                shrink-0
                items-center
                justify-center
                rounded-xl
                text-[var(--text-primary)]
                transition
                hover:bg-[var(--surface-hover)]
                md:hidden
              "
            >
              <Menu size={21} />
            </button>

            <nav
              aria-label="Chat workspace views"
              className="
                min-w-0
                flex-1
                overflow-x-auto
                [scrollbar-width:none]
                [&::-webkit-scrollbar]:hidden
                md:absolute
                md:left-1/2
                md:top-0
                md:h-full
                md:-translate-x-1/2
              "
            >
              <div
                className="
                  flex
                  h-14
                  w-max
                  min-w-full
                  items-stretch
                  justify-start
                  gap-5
                  px-1
                  md:h-full
                  md:min-w-0
                  md:justify-center
                  md:gap-7
                "
              >
                <button
                  type="button"
                  onClick={openChatView}
                  className={`
                    relative
                    flex
                    h-full
                    shrink-0
                    items-center
                    px-0.5
                    text-sm
                    transition-colors
                    duration-150

                    ${
                      activeView === "chat"
                        ? "font-semibold text-[var(--text-primary)]"
                        : "font-medium text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
                    }
                  `}
                >
                  Chat

                  {activeView === "chat" && (
                    <span
                      className="
                        absolute
                        bottom-0
                        left-0
                        right-0
                        h-0.5
                        rounded-full
                        bg-[var(--text-primary)]
                      "
                    />
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    void openSummaryView();
                  }}
                  className={`
                    relative
                    flex
                    h-full
                    shrink-0
                    items-center
                    px-0.5
                    text-sm
                    transition-colors
                    duration-150

                    ${
                      activeView === "summary"
                        ? "font-semibold text-[var(--text-primary)]"
                        : "font-medium text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
                    }
                  `}
                >
                  Summary

                  {activeView === "summary" && (
                    <span
                      className="
                        absolute
                        bottom-0
                        left-0
                        right-0
                        h-0.5
                        rounded-full
                        bg-[var(--text-primary)]
                      "
                    />
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    void openTranscriptionView();
                  }}
                  className={`
                    relative
                    flex
                    h-full
                    shrink-0
                    items-center
                    px-0.5
                    text-sm
                    transition-colors
                    duration-150

                    ${
                      activeView === "transcription"
                        ? "font-semibold text-[var(--text-primary)]"
                        : "font-medium text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
                    }
                  `}
                >
                  Transcription

                  {activeView === "transcription" && (
                    <span
                      className="
                        absolute
                        bottom-0
                        left-0
                        right-0
                        h-0.5
                        rounded-full
                        bg-[var(--text-primary)]
                      "
                    />
                  )}
                </button>
              </div>
            </nav>

            <div
              className="
                ml-1
                shrink-0
                md:ml-auto
              "
            >
              {workspaceChat && (
                <ChatHeaderActions
                  filesOpen={
                    filesOpen
                  }

                  onToggleFiles={() => {
                    setFilesOpen(
                      (
                        current
                      ) => !current
                    );
                  }}

                  onRename={
                    renameCurrentChat
                  }

                  onDelete={
                    deleteCurrentChat
                  }
                />
              )}
            </div>
          </header>


          {activeView === "chat" ? (
            <>
              <section
                ref={messagesContainerRef}
                className="
                  relative
                  min-h-0
                  flex-1
                  overflow-y-auto
                "
              >
                {chatLoading ? (
                  <div
                    className="
                      flex
                      h-full
                      items-center
                      justify-center
                    "
                  >
                    <div
                      className="
                        flex
                        items-center
                        gap-2
                        rounded-full
                        bg-[var(--surface)]
                        px-4
                        py-2
                        text-xs
                        text-[var(--text-muted)]
                        transition-colors
                        duration-200
                      "
                    >
                      <Loader2
                        size={14}
                        className="
                          animate-spin
                          text-[var(--primary)]
                        "
                      />

                      Loading messages...
                    </div>
                  </div>

                ) : !workspaceChat ? (
                  <div
                    className="
                      flex
                      h-full
                      items-center
                      justify-center
                      px-6
                      text-center
                    "
                  >
                    <div>
                      <div
                        className="
                          mx-auto
                          mb-3
                          flex
                          h-10
                          w-10
                          items-center
                          justify-center
                          rounded-xl
                          bg-red-500/10
                          text-red-500
                        "
                      >
                        <Sparkles
                          size={17}
                        />
                      </div>


                      <p
                        className="
                          text-sm
                          font-medium
                          text-[var(--text-primary)]
                        "
                      >
                        Could not load this chat
                      </p>


                      <p
                        className="
                          mt-1
                          text-xs
                          text-[var(--text-muted)]
                        "
                      >
                        Try opening another chat.
                      </p>
                    </div>
                  </div>

                ) : (
                  <div
                    className="
                      mx-auto
                      flex
                      min-h-full
                      w-full
                      max-w-3xl
                      flex-col
                      px-3
                      py-4
                      sm:px-5
                      sm:py-6
                      md:px-6
                    "
                  >
                    {messages.length ===
                    0 ? (
                      <div
                        className="
                          flex
                          flex-1
                          flex-col
                          items-center
                          justify-center
                          pb-6
                          text-center
                        "
                      >
                        <h2
                          className="
                            text-xl
                            font-semibold
                            sm:text-2xl
                            tracking-tight
                            text-[var(--text-primary)]
                          "
                        >
                          What&apos;s on your mind today?
                        </h2>


                        <p
                          className="
                            mt-2
                            max-w-sm
                            text-sm
                            leading-6
                            text-[var(--text-secondary)]
                          "
                        >
                          Ask anything or attach files
                          to work with your documents.
                        </p>
                      </div>

                    ) : (
                      <div
                        className="
                          space-y-8
                          pb-8
                        "
                      >
                        {messages.map(
                          (
                            message
                          ) => (
                            <ChatMessage
                              key={
                                message.id
                              }

                              message={
                                message
                              }
                            />
                          )
                        )}


                        <div
                          ref={
                            messagesEndRef
                          }
                        />
                      </div>
                    )}
                  </div>
                )}

              </section>


              {showScrollToBottom && (
                <button
                  type="button"
                  onClick={scrollToBottom}
                  title="Scroll to latest message"
                  aria-label="Scroll to latest message"
                  className="absolute bottom-[calc(6rem+env(safe-area-inset-bottom))] left-1/2 z-20 flex h-10 w-10 -translate-x-1/2 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] shadow-lg transition hover:bg-[var(--surface-hover)]"
                >
                  <ArrowDown size={18} />
                </button>
              )}


              {workspaceChat && (
                <ChatComposer
                  question={
                    question
                  }

                  sending={
                    sending
                  }

                  uploading={
                    uploading
                  }

                  attachment={
                    attachment
                  }

                  documentsProcessing={
                    documentsProcessing
                  }

                  allowGeneralKnowledge={
                    allowGeneralKnowledge
                  }

                  composerError={
                    composerError
                  }

                  fileInputRef={
                    fileInputRef
                  }

                  onQuestionChange={
                    setQuestion
                  }

                  onSetGeneralKnowledge={
                    setAllowGeneralKnowledge
                  }

                  onUpload={
                    handleUpload
                  }

                  onRemoveAttachment={
                    handleRemoveAttachment
                  }

                  onSubmit={
                    sendQuestion
                  }
                />
              )}
            </>

          ) : (
            <>
              {selectedDocument
              && summaryToken
              && chat ? (
                <DocumentSummaryPanel
                  key={
                    `${selectedDocument.id}-${activeView}`
                  }

                  open={
                    true
                  }

                  chatId={
                    chatId
                  }

                  documentId={
                    selectedDocument.id
                  }

                  filename={
                    selectedDocument.filename
                  }

                  pagesCount={
                    selectedDocument.pages_count
                  }

                  token={
                    summaryToken
                  }

                  mode={
                    activeView
                    === "transcription"
                      ? "transcription"
                      : "summary"
                  }

                  uploading={
                    uploading
                  }

                  fileInputRef={
                    fileInputRef
                  }

                  onUpload={
                    handleUpload
                  }

                  documents={
                    chat.documents
                  }

                  onSelectDocument={(
                    document
                  ) => {
                    const nextDocument =
                      chat.documents.find(
                        (item) =>
                          item.id
                          === document.id
                      );

                    if (!nextDocument) {
                      return;
                    }

                    void openDocumentView(
                      nextDocument,
                      activeView
                      === "transcription"
                        ? "transcription"
                        : "summary"
                    );
                  }}
                />

              ) : (
                <section
                  className="
                    flex
                    min-h-0
                    flex-1
                    items-center
                    justify-center
                    px-6
                  "
                >
                  <div
                    className="
                      w-full
                      max-w-2xl
                    "
                  >
                    <p
                      className="
                        text-xs
                        font-medium
                        uppercase
                        tracking-[0.18em]
                        text-[var(--text-muted)]
                      "
                    >
                      {
                        activeView
                        === "transcription"
                          ? "Transcription"
                          : "Summary"
                      }
                    </p>


                    <h2
                      className="
                        mt-3
                        text-2xl
                        font-semibold
                        leading-tight
                        text-[var(--text-primary)]
                        sm:text-3xl
                      "
                    >
                      {
                        activeView
                        === "transcription"
                          ? "Read your document in detail"
                          : "Understand your document faster"
                      }
                    </h2>


                    <p
                      className="
                        mt-4
                        max-w-xl
                        text-[15px]
                        leading-7
                        text-[var(--text-secondary)]
                      "
                    >
                      {
                        activeView
                        === "transcription"
                          ? (
                            "Transcription works through the document page by page and keeps the important detail, including tables, charts, images, equations, and technical content."
                          )
                          : (
                            "Summary condenses the document into its main ideas, key points, conclusions, and the most important takeaway from each section."
                          )
                      }
                    </p>


                    {workspaceChat
                    && workspaceChat.documents.length === 0 ? (
                      <div
                        className="
                          mt-7
                        "
                      >
                        <input
                          ref={
                            fileInputRef
                          }
                          type="file"
                          accept=".pdf,.docx,.xlsx,.txt"
                          onChange={
                            handleUpload
                          }
                          className="hidden"
                        />

                        <button
                          type="button"
                          onClick={() =>
                            fileInputRef
                              .current
                              ?.click()
                          }
                          disabled={
                            uploading
                            || Boolean(
                              attachment
                            )
                          }
                          className="
                            inline-flex
                            min-h-10
                            items-center
                            justify-center
                            rounded-xl
                            bg-[var(--primary)]
                            px-5
                            py-2.5
                            text-sm
                            font-medium
                            text-white
                            transition
                            hover:opacity-90
                            disabled:cursor-not-allowed
                            disabled:opacity-50
                          "
                        >
                          {
                            uploading
                              ? "Uploading..."
                              : attachment
                                ? "Processing file..."
                                : "Upload a file"
                          }
                        </button>

                        <p
                          className="
                            mt-3
                            text-xs
                            leading-5
                            text-[var(--text-muted)]
                          "
                        >
                          PDF, DOCX, XLSX, or TXT
                        </p>
                      </div>

                    ) : workspaceChat
                    && workspaceChat.documents.length > 0 ? (
                      <button
                        type="button"
                        onClick={() => {
                          setFilesOpen(
                            true
                          );
                        }}
                        className="
                          mt-7
                          rounded-xl
                          bg-[var(--primary)]
                          px-5
                          py-2.5
                          text-sm
                          font-medium
                          text-white
                          transition
                          hover:opacity-90
                        "
                      >
                        Choose document
                      </button>

                    ) : null}
                  </div>
                </section>
              )}
            </>
          )}
        </div>


        {chat && (
          <UploadedDocsPanel
            open={
              filesOpen
            }

            documents={
              chat.documents
            }

            onClose={() =>
              setFilesOpen(
                false
              )
            }

            onRemove={
              handleRemoveDocument
            }

            onOpenSummary={
              openDocumentSummary
            }
          />
        )}
      </section>
    </main>
  );
}