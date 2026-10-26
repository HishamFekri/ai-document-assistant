"use client";

import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import {
  createPortal,
} from "react-dom";

import {
  Loader2,
  Paperclip,
  Plus,
} from "lucide-react";

import {
  type UploadPolicy,
  uploadMenuDescription,
} from "@/lib/upload-policy";


const GAP = 8;
const EDGE = 12;


// The composer's + button. It opens a small menu whose only action is the
// existing file picker, with the upload limits as its description, so the
// limits no longer sit permanently under the composer.
export default function UploadMenu({
  policy,
  uploading,
  disabled,
  onChooseFile,
}: {
  policy: UploadPolicy | null;
  uploading: boolean;
  disabled: boolean;
  onChooseFile: () => void;
}) {
  const [requestedOpen, setOpen] = useState(false);
  // Closes by itself while the action is unavailable (e.g. during a send).
  const open = requestedOpen && !disabled;
  const [position, setPosition] = useState({ left: EDGE, bottom: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const descriptionId = useId();

  function close(returnFocus: boolean) {
    setOpen(false);

    if (returnFocus) {
      buttonRef.current?.focus();
    }
  }

  // Above the + button, kept inside the viewport on narrow screens.
  useLayoutEffect(() => {
    if (!open) {
      return;
    }

    const place = () => {
      const button = buttonRef.current?.getBoundingClientRect();
      const width = menuRef.current?.offsetWidth ?? 0;

      if (!button) {
        return;
      }

      setPosition({
        left: Math.max(EDGE, Math.min(button.left, window.innerWidth - width - EDGE)),
        bottom: window.innerHeight - button.top + GAP,
      });
    };

    place();
    itemRef.current?.focus();
    window.addEventListener("resize", place);

    return () => window.removeEventListener("resize", place);
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;

      if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) {
        setOpen(false);
      }
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
      }
    };

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            event.preventDefault();
            setOpen(true);
          }
        }}
        disabled={disabled}
        title="Add files"
        aria-label="Add files"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        className="
          flex
          h-9
          w-9
          shrink-0
          items-center
          justify-center
          rounded-full
          text-[var(--text-secondary)]
          transition
          hover:bg-[var(--surface-hover)]
          hover:text-[var(--text-primary)]
          disabled:cursor-not-allowed
          disabled:opacity-40
        "
      >
        {uploading ? (
          <Loader2
            size={18}
            className="
              animate-spin
            "
          />
        ) : (
          <Plus
            size={19}
            className={`
              transition-transform
              duration-200
              ${open ? "rotate-45" : ""}
            `}
          />
        )}
      </button>

      {open && createPortal(
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label="Add files"
          style={{ left: position.left, bottom: position.bottom }}
          className="
            aura
            aura-popover-in
            fixed
            z-[9999]
            w-[min(22.5rem,calc(100vw-1.5rem))]
            overflow-hidden
            rounded-xl
            border
            border-[var(--border)]
            bg-[var(--menu)]
            p-1.5
            shadow-xl
            shadow-black/20
          "
        >
          <button
            ref={itemRef}
            type="button"
            role="menuitem"
            aria-describedby={descriptionId}
            onClick={() => {
              close(false);
              onChooseFile();
            }}
            onKeyDown={(event) => {
              if (event.key === "Tab") {
                close(false);
              }
            }}
            className="
              flex
              w-full
              items-start
              gap-2.5
              rounded-lg
              px-2.5
              py-2
              text-left
              transition
              hover:bg-[var(--surface-hover)]
              focus-visible:bg-[var(--surface-hover)]
              focus-visible:outline-none
            "
          >
            <Paperclip
              size={15}
              aria-hidden="true"
              className="
                mt-0.5
                shrink-0
                text-[var(--text-secondary)]
              "
            />

            <span className="flex min-w-0 flex-col gap-0.5">
              <span
                className="
                  text-[13px]
                  font-medium
                  text-[var(--text-primary)]
                "
              >
                Upload file
              </span>

              {policy && (
                <span
                  id={descriptionId}
                  className="
                    text-[11px]
                    leading-4
                    text-[var(--text-muted)]
                  "
                >
                  {/* Lines only break between parts, never inside "500 PDF pages max". */}
                  {uploadMenuDescription(policy).split(" · ").map((part, index) => (
                    <span key={part}>
                      {index > 0 && " · "}
                      <span className="whitespace-nowrap">{part}</span>
                    </span>
                  ))}
                </span>
              )}
            </span>
          </button>
        </div>,
        document.body
      )}
    </>
  );
}
