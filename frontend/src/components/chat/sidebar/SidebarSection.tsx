"use client";

import {
  ChevronDown,
} from "lucide-react";

import {
  Dispatch,
  SetStateAction,
} from "react";

import {
  ChatListItem,
} from "@/types/chat";

import ChatRow from "@/components/chat/sidebar/ChatRow";


type Props = {
  title: string;
  hideHeader?: boolean;
  chats: ChatListItem[];
  activeChatId: number;
  open: boolean;
  onToggle: () => void;

  openMenuId:
    number | null;

  setOpenMenuId:
    Dispatch<
      SetStateAction<
        number | null
      >
    >;

  editingChatId:
    number | null;

  editingTitle: string;

  setEditingTitle:
    Dispatch<
      SetStateAction<string>
    >;

  deletingChatId:
    number | null;

  workingChatId:
    number | null;

  emptyText?: string;

  onOpenChat: (
    chatId: number
  ) => void;

  onStartRename: (
    chat: ChatListItem
  ) => void;

  onSaveRename: (
    chatId: number
  ) => Promise<void>;

  onCancelRename: () => void;

  onPin: (
    chatId: number
  ) => Promise<void>;

  onArchive: (
    chatId: number
  ) => Promise<void>;

  onDelete: (
    chatId: number
  ) => Promise<void>;
};


export default function SidebarSection({
  title,
  hideHeader = false,
  chats,
  activeChatId,
  open,
  onToggle,
  openMenuId,
  setOpenMenuId,
  editingChatId,
  editingTitle,
  setEditingTitle,
  deletingChatId,
  workingChatId,
  emptyText = "No chats",
  onOpenChat,
  onStartRename,
  onSaveRename,
  onCancelRename,
  onPin,
  onArchive,
  onDelete,
}: Props) {
  return (
    <section
      className={`${
        hideHeader
          ? "mt-0"
          : "mt-4"
      } ${
        title === "Archived"
          ? "order-first"
          : ""
      }`}
    >

      {!hideHeader && (
        <button
          type="button"
          onClick={
            onToggle
          }
          className="
            group
            flex
            w-full
            items-center
            justify-between
            rounded-lg
            px-3
            py-1.5
            text-left
            transition
            hover:bg-[var(--surface-hover)]
          "
        >
          <span
            className="
              text-[11px]
              font-semibold
              uppercase
              tracking-[0.08em]
              text-[var(--text-muted)]
            "
          >
            {title}
          </span>

          <div
            className="
              flex
              items-center
              gap-2
              opacity-0
              transition-opacity
              duration-150
              group-hover:opacity-100
            "
          >
            {chats.length > 0 && (
              <span
                className="
                  min-w-5
                  rounded-full
                  bg-[var(--surface)]
                  px-1.5
                  py-0.5
                  text-center
                  text-[9px]
                  font-medium
                  text-[var(--text-muted)]
                "
              >
                {chats.length}
              </span>
            )}

            <ChevronDown
              size={13}
              className={`
                text-[var(--text-muted)]
                transition-transform
                duration-200
                ${
                  open
                    ? "rotate-180"
                    : "rotate-0"
                }
              `}
            />
          </div>
        </button>
      )}


      <div
        className={`
          grid
          transition-all
          duration-200
          ease-in-out
          ${
            open
              ? "grid-rows-[1fr] opacity-100"
              : "grid-rows-[0fr] opacity-0"
          }
        `}
      >
        <div className="overflow-hidden">

          <div className="pt-1">

            {chats.length === 0 ? (
              <div
                className="
                  px-3
                  py-2
                  text-[11px]
                  text-[var(--text-muted)]
                "
              >
                {emptyText}
              </div>
            ) : (
              <div className="space-y-0.5">

                {chats.map(
                  (chat) => (
                    <ChatRow
                      key={chat.id}
                      chat={chat}
                      active={
                        chat.id ===
                        activeChatId
                      }
                      menuOpen={
                        openMenuId ===
                        chat.id
                      }
                      editing={
                        editingChatId ===
                        chat.id
                      }
                      editingTitle={
                        editingTitle
                      }
                      confirmingDelete={
                        deletingChatId ===
                        chat.id
                      }
                      working={
                        workingChatId ===
                        chat.id
                      }
                      setEditingTitle={
                        setEditingTitle
                      }
                      onOpenChat={
                        onOpenChat
                      }
                      onToggleMenu={(
                        chatId
                      ) =>
                        setOpenMenuId(
                          (
                            current
                          ) =>
                            current ===
                            chatId
                              ? null
                              : chatId
                        )
                      }
                      onStartRename={
                        onStartRename
                      }
                      onSaveRename={
                        onSaveRename
                      }
                      onCancelRename={
                        onCancelRename
                      }
                      onPin={
                        onPin
                      }
                      onArchive={
                        onArchive
                      }
                      onDelete={
                        onDelete
                      }
                    />
                  )
                )}

              </div>
            )}

          </div>
        </div>
      </div>
    </section>
  );
}