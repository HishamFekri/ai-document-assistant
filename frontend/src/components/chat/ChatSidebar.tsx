"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  ChatListItem,
  User,
} from "@/types/chat";

import SidebarHeader from "@/components/chat/sidebar/SidebarHeader";
import SidebarSearch from "@/components/chat/sidebar/SidebarSearch";
import SidebarNavigation from "@/components/chat/sidebar/SidebarNavigation";
import SidebarSection from "@/components/chat/sidebar/SidebarSection";
import SidebarUser from "@/components/chat/sidebar/SidebarUser";
import CollapsedSidebar from "@/components/chat/sidebar/CollapsedSidebar";


type Props = {
  user: User | null;

  chats: ChatListItem[];

  activeChatId: number;

  creatingChat: boolean;

  mobile?: boolean;

  onMobileClose?: () => void;

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
  mobile = false,
  onMobileClose,
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


  const activeChat =
    useMemo(
      () =>
        chats.find(
          (chat) =>
            chat.id ===
            activeChatId
        ) ?? null,
      [
        chats,
        activeChatId,
      ]
    );


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
    filteredChats.filter(
      (chat) =>
        chat.is_archived
    );


  useEffect(() => {
    if (activeChat?.is_archived || archivedChats.length > 0) {
      const timeoutId = window.setTimeout(() => {
        setArchiveOpen(true);
      }, 0);

      return () => window.clearTimeout(timeoutId);
    }
  }, [
    activeChat,
    archivedChats.length,
  ]);


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


  if (
    collapsed
    && !mobile
  ) {
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

        onCreateChat={() => {
          onCreateChat();

          if (mobile) {
            onMobileClose?.();
          }
        }}

        onLogout={
          onLogout
        }
      />
    );
  }


  return (
    <aside
      className={`
        flex
        shrink-0
        flex-col
        border-r
        border-[var(--border)]
        bg-[var(--sidebar)]

        ${
          mobile
            ? "h-full w-[min(86vw,320px)] max-w-[320px]"
            : "h-screen w-[250px]"
        }
      `}
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


      <SidebarNavigation
        creatingChat={
          creatingChat
        }

        archivedChats={
          archivedChats
        }

        activeChatId={
          activeChatId
        }

        archiveOpen={
          archiveOpen
        }

        onCreateChat={
          onCreateChat
        }

        onToggleArchive={() =>
          setArchiveOpen(
            (
              current
            ) => !current
          )
        }

        onOpenChat={(chatId) => {
          onOpenChat(chatId);

          if (mobile) {
            onMobileClose?.();
          }
        }}

        archivedContent={
          <SidebarSection
            title="Archived"
            hideHeader
            chats={archivedChats}
            activeChatId={activeChatId}
            open={true}
            onToggle={() => setArchiveOpen(false)}
            openMenuId={openMenuId}
            setOpenMenuId={setOpenMenuId}
            editingChatId={editingChatId}
            editingTitle={editingTitle}
            setEditingTitle={setEditingTitle}
            deletingChatId={deletingChatId}
            workingChatId={workingChatId}
            emptyText="No archived chats"
            onOpenChat={(chatId) => {
              onOpenChat(chatId);

              if (mobile) {
                onMobileClose?.();
              }
            }}
            onStartRename={startRename}
            onSaveRename={saveRename}
            onCancelRename={cancelRename}
            onPin={handlePin}
            onArchive={handleArchive}
            onDelete={handleDelete}
          />
        }
      />


      <div
        className="
          min-h-0
          flex-1
          flex
          flex-col
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

          onOpenChat={(chatId) => {
            onOpenChat(chatId);

            if (mobile) {
              onMobileClose?.();
            }
          }}

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

          onOpenChat={(chatId) => {
            onOpenChat(chatId);

            if (mobile) {
              onMobileClose?.();
            }
          }}

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