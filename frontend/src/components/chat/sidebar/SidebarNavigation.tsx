"use client";

import {
  Archive,
  ChevronDown,
  MessageSquarePlus,
} from "lucide-react";

import {
  ChatListItem,
} from "@/types/chat";

import type {
  ReactNode,
} from "react";


type Props = {
  creatingChat: boolean;

  archivedChats: ChatListItem[];

  activeChatId: number;

  archiveOpen: boolean;

  onCreateChat: () => void;

  onToggleArchive: () => void;

  onOpenChat: (
    chatId: number
  ) => void;

  archivedContent?: ReactNode;
};


export default function SidebarNavigation(
  props: Props
) {
  const {
    creatingChat,
    archivedChats,
    archiveOpen,
    onCreateChat,
    onToggleArchive,
    archivedContent,
  } = props;

  return (
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
        onClick={
          onToggleArchive
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
          transition-[grid-template-rows,opacity]
          duration-200
          ease-out
          ${
            archiveOpen
              ? "grid-rows-[1fr] opacity-100"
                : "grid-rows-[0fr] opacity-0"
          }
        `}
      >
          <div
            className={`
              min-h-0
              overflow-hidden
              ${
                archiveOpen
                  ? "-mt-1 rounded-b-xl bg-[var(--surface)] shadow-lg shadow-black/10"
                  : ""
              }
            `}
          >
          {archivedContent}
        </div>
      </div>
    </div>
  );
}