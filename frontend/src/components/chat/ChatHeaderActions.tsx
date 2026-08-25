"use client";

import {
  FileText,
  MoreHorizontal,
  Pencil,
  Trash2,
  X,
} from "lucide-react";

import {
  useEffect,
  useRef,
  useState,
} from "react";


type Props = {
  filesOpen: boolean;

  onToggleFiles: () => void;

  onRename: () => void;

  onDelete: () => void;
};


export default function ChatHeaderActions({
  filesOpen,
  onToggleFiles,
  onRename,
  onDelete,
}: Props) {
  const [
    menuOpen,
    setMenuOpen,
  ] = useState(false);

  const menuRef =
    useRef<HTMLDivElement | null>(
      null
    );


  useEffect(() => {
    function handleClickOutside(
      event: MouseEvent
    ) {
      if (
        menuRef.current
        && !menuRef.current.contains(
          event.target as Node
        )
      ) {
        setMenuOpen(false);
      }
    }

    document.addEventListener(
      "mousedown",
      handleClickOutside
    );

    return () => {
      document.removeEventListener(
        "mousedown",
        handleClickOutside
      );
    };
  }, []);


  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={
          onToggleFiles
        }
        title={
          filesOpen
            ? "Close files"
            : "View files in chat"
        }
        className={`
          flex
          h-9
          items-center
          gap-2
          rounded-full
          px-3
          text-sm
          transition-all
          duration-200
          active:scale-95

          ${
            filesOpen
              ? "bg-[var(--primary-soft)] text-[var(--primary)]"
              : "text-[var(--text-primary)] hover:bg-[var(--surface-hover)]"
          }
        `}
      >
        {filesOpen ? (
          <X
            size={16}
            className="
              text-[var(--primary)]
            "
          />
        ) : (
          <FileText
            size={16}
            className="
              text-[var(--text-primary)]
            "
          />
        )}

        <span
          className="
            hidden
            sm:inline
            text-[var(--text-primary)]
          "
        >
          Files
        </span>
      </button>


      <div
        ref={
          menuRef
        }
        className="relative"
      >
        <button
          type="button"
          onClick={() =>
            setMenuOpen(
              (
                current
              ) => !current
            )
          }
          title="Chat options"
          className={`
            flex
            h-9
            w-9
            items-center
            justify-center
            rounded-full
            transition-all
            duration-200
            active:scale-95

            ${
              menuOpen
                ? "bg-[var(--surface-active)] text-[var(--text-primary)]"
                : "text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
            }
          `}
        >
          <MoreHorizontal
            size={18}
          />
        </button>


        {menuOpen && (
          <div
            className="
              absolute
              right-0
              top-11
              z-50
              w-52
              rounded-2xl
              border
              border-[var(--border)]
              bg-[var(--menu)]
              p-1.5
              text-[var(--text-primary)]
              shadow-2xl
              shadow-black/20
            "
          >
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);

                onToggleFiles();
              }}
              className="
                flex
                w-full
                items-center
                gap-3
                rounded-xl
                px-3
                py-2.5
                text-left
                text-sm
                text-[var(--text-primary)]
                transition
                hover:bg-[var(--menu-hover)]
              "
            >
              <FileText
                size={16}
              />

              View files in chat
            </button>


            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);

                onRename();
              }}
              className="
                flex
                w-full
                items-center
                gap-3
                rounded-xl
                px-3
                py-2.5
                text-left
                text-sm
                text-[var(--text-primary)]
                transition
                hover:bg-[var(--menu-hover)]
              "
            >
              <Pencil
                size={16}
              />

              Rename
            </button>


            <div
              className="
                my-1
                border-t
                border-[var(--border)]
              "
            />


            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);

                onDelete();
              }}
              className="
                flex
                w-full
                items-center
                gap-3
                rounded-xl
                px-3
                py-2.5
                text-left
                text-sm
                font-medium
                text-red-500
                transition
                hover:bg-red-500/10
              "
            >
              <Trash2
                size={16}
                className="
                  text-red-500
                "
              />

              <span
                className="
                  text-red-500
                "
              >
                Delete
              </span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}