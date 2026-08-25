"use client";

import {
  Search,
  X,
} from "lucide-react";


type Props = {
  open: boolean;

  value: string;

  onChange: (
    value: string
  ) => void;

  onClose: () => void;
};


export default function SidebarSearch({
  open,
  value,
  onChange,
  onClose,
}: Props) {
  if (!open) {
    return null;
  }

  return (
    <div className="px-4 pb-2">

      <div className="relative">

        <Search
          size={15}
          strokeWidth={1.8}
          className="
            absolute
            left-3
            top-1/2
            -translate-y-1/2
            text-[var(--text-muted)]
          "
        />


        <input
          autoFocus
          value={value}
          onChange={(
            event
          ) =>
            onChange(
              event.target.value
            )
          }
          placeholder="Search chats"
          className="
            h-9
            w-full
            rounded-xl
            border
            border-[var(--border)]
            bg-[var(--surface)]
            pl-9
            pr-9
            text-[13px]
            text-[var(--text-primary)]
            outline-none
            transition
            placeholder:text-[var(--text-muted)]
            focus:border-[var(--primary)]
            focus:ring-2
            focus:ring-[var(--primary-soft)]
          "
        />


        <button
          type="button"
          onClick={() => {
            if (value) {
              onChange("");
              return;
            }

            onClose();
          }}
          className="
            absolute
            right-2
            top-1/2
            flex
            h-6
            w-6
            -translate-y-1/2
            items-center
            justify-center
            rounded-md
            text-red-400
            transition
            hover:bg-red-500/10
            hover:text-red-500
          "
        >
          <X
            size={14}
          />
        </button>

      </div>
    </div>
  );
}