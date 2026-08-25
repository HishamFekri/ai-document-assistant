"use client";

import {
  useMemo,
  useState,
} from "react";

import {
  Archive,
  ChevronDown,
  MessageSquarePlus,
} from "lucide-react";

import {
  ChatListItem,
  User,
} from "@/types/chat";

import SidebarHeader from "@/components/chat/sidebar/SidebarHeader";
import SidebarSearch from "@/components/chat/sidebar/SidebarSearch";
import SidebarSection from "@/components/chat/sidebar/SidebarSection";
import SidebarUser from "@/components/chat/sidebar/SidebarUser";
import CollapsedSidebar from "@/components/chat/sidebar/CollapsedSidebar";


type Props = {
  user: User | null;

  chats: ChatListItem[];

  activeChatId: number;

  creatingChat: boolean;

  onCreateChat: () => void;

  onOpenChat: (
    chatId: number
  ) => void;

  onRenameChat: (
    chatId: number,
    title: string
  ) => Promise<void>;

  onPinChat: (
    chatId: number
  ) => Promise<void>;

  onArchiveChat: (
    chatId: number
  ) => Promise<void>;

  onDeleteChat: (
    chatId: number
  ) => Promise<void>;

  onLogout: () => void;
};


export default function ChatSidebar({
  user,
  chats,
  activeChatId,
  creatingChat,
  onCreateChat,
  onOpenChat,
  onRenameChat,
  onPinChat,
  onArchiveChat,
  onDeleteChat,
  onLogout,
}: Props) {
  const [
    collapsed,
    setCollapsed,
  ] = useState(false);


  const [
    searchOpen,
    setSearchOpen,
  ] = useState(false);


  const [
    searchQuery,
    setSearchQuery,
  ] = useState("");


  const [
    archiveOpen,
    setArchiveOpen,
  ] = useState(false);


  const [
    pinnedOpen,
    setPinnedOpen,
  ] = useState(true);


  const [
    recentOpen,
    setRecentOpen,
  ] = useState(true);


  const [
    openMenuId,
    setOpenMenuId,
  ] = useState<
    number | null
  >(null);


  const [
    editingChatId,
    setEditingChatId,
  ] = useState<
    number | null
  >(null);


  const [
    editingTitle,
    setEditingTitle,
  ] = useState("");


  const [
    deletingChatId,
    setDeletingChatId,
  ] = useState<
    number | null
  >(null);


  const [
    workingChatId,
    setWorkingChatId,
  ] = useState<
    number | null
  >(null);


  const filteredChats =
    useMemo(() => {
      const query =
        searchQuery
          .trim()
          .toLowerCase();

      if (!query) {
        return chats;
      }

      return chats.filter(
        (chat) =>
          (
            chat.title
            || "New chat"
          )
            .toLowerCase()
            .includes(
              query
            )
      );
    }, [
      chats,
      searchQuery,
    ]);


  const pinnedChats =
    useMemo(
      () =>
        filteredChats.filter(
          (chat) =>
            chat.is_pinned
            && !chat.is_archived
        ),
      [
        filteredChats,
      ]
    );


  const recentChats =
    useMemo(
      () =>
        filteredChats.filter(
          (chat) =>
            !chat.is_pinned
            && !chat.is_archived
        ),
      [
        filteredChats,
      ]
    );


  const archivedChats =
    useMemo(
      () =>
        filteredChats.filter(
          (chat) =>
            chat.is_archived
        ),
      [
        filteredChats,
      ]
    );


  function startRename(
    chat: ChatListItem
  ) {
    setEditingChatId(
      chat.id
    );

    setEditingTitle(
      chat.title
      || "New chat"
    );

    setOpenMenuId(
      null
    );
  }


  async function saveRename(
    chatId: number
  ) {
    const title =
      editingTitle.trim();

    if (!title) {
      return;
    }

    try {
      setWorkingChatId(
        chatId
      );

      await onRenameChat(
        chatId,
        title
      );

      setEditingChatId(
        null
      );

      setEditingTitle("");

    } finally {
      setWorkingChatId(
        null
      );
    }
  }


  function cancelRename() {
    setEditingChatId(
      null
    );

    setEditingTitle("");
  }


  async function handlePin(
    chatId: number
  ) {
    try {
      setWorkingChatId(
        chatId
      );

      setOpenMenuId(
        null
      );

      await onPinChat(
        chatId
      );

    } finally {
      setWorkingChatId(
        null
      );
    }
  }


  async function handleArchive(
    chatId: number
  ) {
    try {
      setWorkingChatId(
        chatId
      );

      setOpenMenuId(
        null
      );

      await onArchiveChat(
        chatId
      );

    } finally {
      setWorkingChatId(
        null
      );
    }
  }


  async function handleDelete(
    chatId: number
  ) {
    if (
      deletingChatId !==
      chatId
    ) {
      setDeletingChatId(
        chatId
      );

      setOpenMenuId(
        null
      );

      return;
    }

    try {
      setWorkingChatId(
        chatId
      );

      await onDeleteChat(
        chatId
      );

      setDeletingChatId(
        null
      );

    } finally {
      setWorkingChatId(
        null
      );
    }
  }


  if (collapsed) {
    return (
      <CollapsedSidebar
        user={
          user
        }

        creatingChat={
          creatingChat
        }

        onOpenSidebar={() =>
          setCollapsed(
            false
          )
        }

        onCreateChat={
          onCreateChat
        }

        onLogout={
          onLogout
        }
      />
    );
  }


  return (
    <aside
      className="
        flex
        h-screen
        w-[285px]
        shrink-0
        flex-col
        border-r
        border-neutral-200
        bg-[#f7f7f7]
      "
    >
      <SidebarHeader
        searchOpen={
          searchOpen
        }

        onToggleSearch={() =>
          setSearchOpen(
            (
              current
            ) => !current
          )
        }

        onCollapse={() =>
          setCollapsed(
            true
          )
        }
      />


      <SidebarSearch
        open={
          searchOpen
        }

        value={
          searchQuery
        }

        onChange={
          setSearchQuery
        }

        onClose={() => {
          setSearchOpen(
            false
          );

          setSearchQuery("");
        }}
      />


      <div
        className="
          px-2
          pt-2
        "
      >
        <button
          type="button"
          onClick={
            onCreateChat
          }
          disabled={
            creatingChat
          }
          className="
            group
            flex
            w-full
            items-center
            gap-3
            rounded-lg
            px-3
            py-2.5
            text-left
            text-[15px]
            font-medium
            text-[var(--text-primary)]
            transition-colors
            duration-150
            hover:bg-[var(--surface-hover)]
            disabled:cursor-not-allowed
            disabled:opacity-50
          "
        >
          <MessageSquarePlus
            size={19}
            strokeWidth={1.8}
            className="
              shrink-0
              text-[var(--text-primary)]
            "
          />

          <span
            className="
              min-w-0
              flex-1
              truncate
            "
          >
            {creatingChat
              ? "Creating..."
              : "New chat"}
          </span>
        </button>


        <button
          type="button"
          onClick={() =>
            setArchiveOpen(
              (current) => !current
            )
          }
          aria-expanded={
            archiveOpen
          }
          className={`
            group
            mt-1
            flex
            w-full
            items-center
            gap-3
            rounded-lg
            px-3
            py-2.5
            text-left
            text-[15px]
            font-normal
            text-[var(--text-primary)]
            transition-colors
            duration-150
            hover:bg-[var(--surface-hover)]

            ${
              archiveOpen
                ? "bg-[var(--surface-hover)]"
                : ""
            }
          `}
        >
          <Archive
            size={19}
            strokeWidth={1.8}
            className="
              shrink-0
              text-[var(--text-primary)]
            "
          />

          <span
            className="
              min-w-0
              flex-1
              truncate
            "
          >
            Archived chats
          </span>

          {archivedChats.length > 0 && (
            <span
              className="
                rounded-full
                bg-[var(--surface-active)]
                px-1.5
                py-0.5
                text-[10px]
                font-medium
                leading-none
                text-[var(--text-muted)]
              "
            >
              {archivedChats.length}
            </span>
          )}

          <ChevronDown
            size={15}
            strokeWidth={1.8}
            className={`
              shrink-0
              text-[var(--text-muted)]
              transition-transform
              duration-200

              ${
                archiveOpen
                  ? "rotate-180"
                  : ""
              }
            `}
          />
        </button>


        <div
          className={`
            grid
            transition-all
            duration-200
            ease-in-out

            ${
              archiveOpen
                ? "grid-rows-[1fr] opacity-100"
                : "grid-rows-[0fr] opacity-0"
            }
          `}
        >
          <div
            className="
              overflow-hidden
            "
          >
            {archivedChats.length > 0 ? (
              <div
                className="
                  ml-[29px]
                  mt-1
                  max-h-52
                  space-y-0.5
                  overflow-y-auto
                  border-l
                  border-[var(--border)]
                  pl-2
                "
              >
                {archivedChats.map(
                  (archivedChat) => {
                    const active =
                      archivedChat.id
                      === activeChatId;

                    const working =
                      workingChatId
                      === archivedChat.id;

                    return (
                      <div
                        key={
                          archivedChat.id
                        }
                        className={`
                          flex
                          min-w-0
                          items-center
                          gap-1
                          rounded-lg
                          transition-colors

                          ${
                            active
                              ? "bg-[var(--surface-active)]"
                              : "hover:bg-[var(--surface-hover)]"
                          }
                        `}
                      >
                        <button
                          type="button"
                          onClick={() =>
                            onOpenChat(
                              archivedChat.id
                            )
                          }
                          disabled={
                            working
                          }
                          className="
                            min-w-0
                            flex-1
                            truncate
                            px-3
                            py-2
                            text-left
                            text-[13px]
                            text-[var(--text-secondary)]
                            transition-colors
                            hover:text-[var(--text-primary)]
                            disabled:opacity-50
                          "
                        >
                          {archivedChat.title
                            || "New chat"}
                        </button>

                        <button
                          type="button"
                          onClick={() => {
                            void handleArchive(
                              archivedChat.id
                            );
                          }}
                          disabled={
                            working
                          }
                          className="
                            mr-1
                            shrink-0
                            rounded-md
                            px-2
                            py-1
                            text-[11px]
                            font-medium
                            text-[var(--primary)]
                            transition-colors
                            hover:bg-[var(--surface-active)]
                            disabled:cursor-not-allowed
                            disabled:opacity-50
                          "
                        >
                          {working
                            ? "..."
                            : "Unarchive"}
                        </button>
                      </div>
                    );
                  }
                )}
              </div>
            ) : (
              archiveOpen && (
                <div
                  className="
                    ml-[41px]
                    py-2
                    text-[11px]
                    text-[var(--text-muted)]
                  "
                >
                  No archived chats
                </div>
              )
            )}
          </div>
        </div>
      </div>


      <div
        className="
          min-h-0
          flex-1
          overflow-y-auto
          px-2
          pb-3
        "
      >
        <SidebarSection
          title="Pinned"

          chats={
            pinnedChats
          }

          activeChatId={
            activeChatId
          }

          open={
            pinnedOpen
          }

          onToggle={() =>
            setPinnedOpen(
              (
                current
              ) => !current
            )
          }

          openMenuId={
            openMenuId
          }

          setOpenMenuId={
            setOpenMenuId
          }

          editingChatId={
            editingChatId
          }

          editingTitle={
            editingTitle
          }

          setEditingTitle={
            setEditingTitle
          }

          deletingChatId={
            deletingChatId
          }

          workingChatId={
            workingChatId
          }

          emptyText="No pinned chats"

          onOpenChat={
            onOpenChat
          }

          onStartRename={
            startRename
          }

          onSaveRename={
            saveRename
          }

          onCancelRename={
            cancelRename
          }

          onPin={
            handlePin
          }

          onArchive={
            handleArchive
          }

          onDelete={
            handleDelete
          }
        />


        <SidebarSection
          title="Recent"

          chats={
            recentChats
          }

          activeChatId={
            activeChatId
          }

          open={
            recentOpen
          }

          onToggle={() =>
            setRecentOpen(
              (
                current
              ) => !current
            )
          }

          openMenuId={
            openMenuId
          }

          setOpenMenuId={
            setOpenMenuId
          }

          editingChatId={
            editingChatId
          }

          editingTitle={
            editingTitle
          }

          setEditingTitle={
            setEditingTitle
          }

          deletingChatId={
            deletingChatId
          }

          workingChatId={
            workingChatId
          }

          emptyText="No recent chats"

          onOpenChat={
            onOpenChat
          }

          onStartRename={
            startRename
          }

          onSaveRename={
            saveRename
          }

          onCancelRename={
            cancelRename
          }

          onPin={
            handlePin
          }

          onArchive={
            handleArchive
          }

          onDelete={
            handleDelete
          }
        />
      </div>


      <SidebarUser
        user={
          user
        }

        onLogout={
          onLogout
        }
      />
    </aside>
  );
}