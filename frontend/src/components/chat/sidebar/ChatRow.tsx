"use client";

import {
  Check,
  MoreHorizontal,
  X,
} from "lucide-react";

import {
  Dispatch,
  SetStateAction,
  useRef,
} from "react";

import {
  ChatListItem,
} from "@/types/chat";

import ChatRowMenu from "@/components/chat/sidebar/ChatRowMenu";


type Props = {
  chat: ChatListItem;

  active: boolean;

  menuOpen: boolean;

  editing: boolean;

  editingTitle: string;

  confirmingDelete: boolean;

  working: boolean;

  setEditingTitle:
    Dispatch<
      SetStateAction<string>
    >;

  onOpenChat: (
    chatId: number
  ) => void;

  onToggleMenu: (
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


export default function ChatRow({
  chat,
  active,
  menuOpen,
  editing,
  editingTitle,
  confirmingDelete,
  working,
  setEditingTitle,
  onOpenChat,
  onToggleMenu,
  onStartRename,
  onSaveRename,
  onCancelRename,
  onPin,
  onArchive,
  onDelete,
}: Props) {
  const menuButtonRef =
    useRef<HTMLButtonElement | null>(
      null
    );


  function closeMenu() {
    if (!menuOpen) {
      return;
    }

    onToggleMenu(
      chat.id
    );
  }


  if (editing) {
    return (
      <div
        className="
          flex
          items-center
          gap-1
          rounded-xl
          border
          border-[var(--border)]
          bg-[var(--surface)]
          px-2
          py-1.5
        "
      >
        <input
          autoFocus
          dir="auto"
          value={
            editingTitle
          }
          onChange={(
            event
          ) =>
            setEditingTitle(
              event.target.value
            )
          }
          onKeyDown={(
            event
          ) => {
            if (
              event.key ===
              "Enter"
            ) {
              onSaveRename(
                chat.id
              );
            }

            if (
              event.key ===
              "Escape"
            ) {
              onCancelRename();
            }
          }}
          style={{
            textAlign: "start",
          }}
          className="
            min-w-0
            flex-1
            bg-transparent
            px-1
            text-sm
            text-[var(--text-primary)]
            outline-none
          "
        />


        <button
          type="button"
          onClick={() =>
            onSaveRename(
              chat.id
            )
          }
          disabled={
            working
          }
          className="
            flex
            h-7
            w-7
            items-center
            justify-center
            rounded-lg
            text-emerald-500
            transition
            hover:bg-[var(--surface-hover)]
            disabled:opacity-50
          "
        >
          <Check
            size={14}
          />
        </button>


        <button
          type="button"
          onClick={
            onCancelRename
          }
          className="
            flex
            h-7
            w-7
            items-center
            justify-center
            rounded-lg
            text-[var(--danger)]
            transition
            hover:bg-red-500/10
          "
        >
          <X
            size={14}
          />
        </button>
      </div>
    );
  }


  if (confirmingDelete) {
    return (
      <div
        className="
          flex
          items-center
          gap-2
          rounded-xl
          border
          border-red-500/20
          bg-red-500/10
          px-3
          py-2
        "
      >
        <span
          className="
            min-w-0
            flex-1
            truncate
            text-[11px]
            text-red-400
          "
        >
          Delete chat?
        </span>


        <button
          type="button"
          onClick={() =>
            onDelete(
              chat.id
            )
          }
          disabled={
            working
          }
          className="
            rounded-lg
            bg-red-600
            px-2
            py-1
            text-[10px]
            font-medium
            text-white
            transition
            hover:bg-red-700
            disabled:opacity-50
          "
        >
          Delete
        </button>
      </div>
    );
  }


  return (
    <div
      className={`
        group
        relative
        flex
        items-center
        rounded-xl
        transition-colors
        duration-150

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
            chat.id
          )
        }
        disabled={
          working
        }
        className="
          flex
          min-w-0
          flex-1
          items-center
          px-3
          py-2.5
          disabled:opacity-50
        "
      >
        <span
          dir="auto"
          style={{
            textAlign: "start",
          }}
          className="
            min-w-0
            flex-1
            truncate
            text-sm
            font-normal
            text-[var(--text-primary)]
          "
        >
          {chat.title
            || "New chat"}
        </span>
      </button>


      <button
        ref={
          menuButtonRef
        }
        type="button"
        disabled={
          working
        }
        onClick={(
          event
        ) => {
          event.stopPropagation();

          onToggleMenu(
            chat.id
          );
        }}
        className={`
          mr-1
          flex
          h-8
          w-8
          shrink-0
          items-center
          justify-center
          rounded-lg
          text-[var(--text-muted)]
          transition-all
          duration-150
          hover:bg-[var(--surface-active)]
          hover:text-[var(--text-primary)]
          disabled:opacity-50

          ${
            chat.is_archived
              ? "opacity-100"
              : menuOpen
                ? "opacity-100"
                : "opacity-100 md:opacity-0 md:group-hover:opacity-100"
          }
        `}
        title={
          chat.is_archived
            ? "Archived chat options"
            : "Chat options"
        }
        aria-label={
          chat.is_archived
            ? "Archived chat options"
            : "Chat options"
        }
      >
        <MoreHorizontal
          size={16}
        />
      </button>


      {menuOpen && (
        <ChatRowMenu
          chat={
            chat
          }

          anchorRef={
            menuButtonRef
          }

          onClose={
            closeMenu
          }

          onPin={
            onPin
          }

          onArchive={
            onArchive
          }

          onRename={
            onStartRename
          }

          onDelete={
            onDelete
          }
        />
      )}
    </div>
  );
}