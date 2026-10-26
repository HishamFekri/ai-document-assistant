"use client";

import {
  PanelLeftClose,
  Search,
} from "lucide-react";


type Props = {
  searchOpen: boolean;

  onToggleSearch: () => void;

  onCollapse: () => void;
};


export default function SidebarHeader({
  searchOpen,
  onToggleSearch,
  onCollapse,
}: Props) {
  return (
    <div
      className="
        flex
        min-h-14
        items-center
        px-3
      "
    >
      <div
        className="
          flex
          min-w-0
          flex-1
          items-center
        "
      >
        <span
          className="
            whitespace-nowrap
            text-[14px]
            font-semibold
            tracking-[-0.03em]
            text-[var(--text-primary)]
          "
        >
          AI Document Assistant
        </span>
      </div>


      <div
        className="
          ml-1
          flex
          shrink-0
          items-center
        "
      >
        <button
          type="button"
          onClick={
            onToggleSearch
          }
          title="Search chats"
          className={`
            flex
            h-7
            w-7
            shrink-0
            items-center
            justify-center
            rounded-md
            transition-all
            duration-150
            active:scale-95

            ${
              searchOpen
                ? "bg-[var(--surface-active)] text-[var(--text-primary)]"
                : "text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
            }
          `}
        >
          <Search
            size={17}
            strokeWidth={1.8}
          />
        </button>


        <button
          type="button"
          onClick={
            onCollapse
          }
          title="Collapse sidebar"
          className="
            flex
            h-7
            w-7
            shrink-0
            items-center
            justify-center
            rounded-md
            text-[var(--text-secondary)]
            transition-all
            duration-150
            hover:bg-[var(--surface-hover)]
            hover:text-[var(--text-primary)]
            active:scale-95
          "
        >
          <PanelLeftClose
            size={17}
            strokeWidth={1.8}
          />
        </button>
      </div>
    </div>
  );
}