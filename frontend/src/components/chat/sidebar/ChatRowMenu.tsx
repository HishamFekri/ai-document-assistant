"use client";

import {
  Archive,
  Pencil,
  Pin,
  PinOff,
  Share2,
  Trash2,
  Undo2,
} from "lucide-react";

import {
  RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import {
  createPortal,
} from "react-dom";

import {
  ChatListItem,
} from "@/types/chat";


type Props = {
  chat: ChatListItem;

  anchorRef:
    RefObject<
      HTMLButtonElement | null
    >;

  onClose: () => void;

  onPin: (
    chatId: number
  ) => Promise<void>;

  onArchive: (
    chatId: number
  ) => Promise<void>;

  onRename: (
    chat: ChatListItem
  ) => void;

  onDelete: (
    chatId: number
  ) => Promise<void>;
};


type Position = {
  top: number;
  left: number;
};


const MENU_WIDTH = 176;
const MENU_HEIGHT = 210;
const GAP = 6;
const SCREEN_PADDING = 8;


export default function ChatRowMenu({
  chat,
  anchorRef,
  onClose,
  onPin,
  onArchive,
  onRename,
  onDelete,
}: Props) {
  const menuRef =
    useRef<HTMLDivElement | null>(
      null
    );

  const [
    mounted,
    setMounted,
  ] = useState(false);

  const [
    position,
    setPosition,
  ] = useState<Position>({
    top: 0,
    left: 0,
  });


  function updatePosition() {
    const button =
      anchorRef.current;

    if (!button) {
      return;
    }

    const rect =
      button.getBoundingClientRect();


    let left =
      rect.right
      - MENU_WIDTH;


    if (
      left
      < SCREEN_PADDING
    ) {
      left =
        SCREEN_PADDING;
    }


    if (
      left
      + MENU_WIDTH
      > window.innerWidth
      - SCREEN_PADDING
    ) {
      left =
        window.innerWidth
        - MENU_WIDTH
        - SCREEN_PADDING;
    }


    const spaceBelow =
      window.innerHeight
      - rect.bottom;


    const spaceAbove =
      rect.top;


    let top =
      rect.bottom
      + GAP;


    if (
      spaceBelow
      < MENU_HEIGHT
      && spaceAbove
      > spaceBelow
    ) {
      top =
        rect.top
        - MENU_HEIGHT
        - GAP;
    }


    if (
      top
      < SCREEN_PADDING
    ) {
      top =
        SCREEN_PADDING;
    }


    if (
      top
      + MENU_HEIGHT
      > window.innerHeight
      - SCREEN_PADDING
    ) {
      top =
        window.innerHeight
        - MENU_HEIGHT
        - SCREEN_PADDING;
    }


    setPosition({
      top,
      left,
    });
  }


  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setMounted(true);
    }, 0);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, []);


  useLayoutEffect(() => {
    if (!mounted) {
      return;
    }

    updatePosition();
  }, [
    mounted,
  ]);


  useEffect(() => {
    if (!mounted) {
      return;
    }


    function handleOutsideClick(
      event: MouseEvent
    ) {
      const target =
        event.target as Node;


      const clickedMenu =
        menuRef.current
        ?.contains(
          target
        );


      const clickedButton =
        anchorRef.current
        ?.contains(
          target
        );


      if (
        clickedMenu
        || clickedButton
      ) {
        return;
      }


      onClose();
    }


    function handleKeyDown(
      event: KeyboardEvent
    ) {
      if (
        event.key
        === "Escape"
      ) {
        onClose();
      }
    }


    function handleWindowChange() {
      updatePosition();
    }


    document.addEventListener(
      "mousedown",
      handleOutsideClick
    );


    document.addEventListener(
      "keydown",
      handleKeyDown
    );


    window.addEventListener(
      "resize",
      handleWindowChange
    );


    window.addEventListener(
      "scroll",
      handleWindowChange,
      true
    );


    return () => {
      document.removeEventListener(
        "mousedown",
        handleOutsideClick
      );


      document.removeEventListener(
        "keydown",
        handleKeyDown
      );


      window.removeEventListener(
        "resize",
        handleWindowChange
      );


      window.removeEventListener(
        "scroll",
        handleWindowChange,
        true
      );
    };
  }, [
    mounted,
    anchorRef,
    onClose,
  ]);


  if (!mounted) {
    return null;
  }


  return createPortal(
    <div
      ref={
        menuRef
      }
      style={{
        top:
          position.top,

        left:
          position.left,
      }}
      className="
        fixed
        z-[9999]
        w-44
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
        type="button"
        onClick={async () => {
          onClose();

          await onPin(
            chat.id
          );
        }}
        className="
          flex
          w-full
          items-center
          gap-2.5
          rounded-lg
          px-2.5
          py-2
          text-left
          text-xs
          text-[var(--text-secondary)]
          transition
          hover:bg-[var(--surface-hover)]
          hover:text-[var(--primary)]
        "
      >
        {chat.is_pinned ? (
          <PinOff
            size={14}
          />
        ) : (
          <Pin
            size={14}
          />
        )}

        {chat.is_pinned
          ? "Unpin"
          : "Pin"}
      </button>


      <button
        type="button"
        onClick={async () => {
          onClose();

          await onArchive(
            chat.id
          );
        }}
        className="
          flex
          w-full
          items-center
          gap-2.5
          rounded-lg
          px-2.5
          py-2
          text-left
          text-xs
          text-[var(--text-secondary)]
          transition
          hover:bg-[var(--surface-hover)]
          hover:text-[var(--text-primary)]
        "
      >
        {chat.is_archived ? (
          <Undo2
            size={14}
          />
        ) : (
          <Archive
            size={14}
          />
        )}

        {chat.is_archived
          ? "Unarchive"
          : "Archive"}
      </button>


      <button
        type="button"
        disabled
        title="Share will be connected later"
        className="
          flex
          w-full
          cursor-not-allowed
          items-center
          gap-2.5
          rounded-lg
          px-2.5
          py-2
          text-left
          text-xs
          text-[var(--text-muted)]
          opacity-50
        "
      >
        <Share2
          size={14}
        />

        Share
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
          onClose();

          onRename(
            chat
          );
        }}
        className="
          flex
          w-full
          items-center
          gap-2.5
          rounded-lg
          px-2.5
          py-2
          text-left
          text-xs
          text-[var(--text-secondary)]
          transition
          hover:bg-[var(--surface-hover)]
          hover:text-[var(--primary)]
        "
      >
        <Pencil
          size={14}
        />

        Rename
      </button>


      <button
        type="button"
        onClick={async () => {
          onClose();

          await onDelete(
            chat.id
          );
        }}
        style={{
          color: "#ef4444",
        }}
        className="
          flex
          w-full
          items-center
          gap-2.5
          rounded-lg
          px-2.5
          py-2
          text-left
          text-xs
          transition
          hover:bg-red-500/10
        "
      >
        <Trash2
          size={14}
          color="#ef4444"
        />

        <span
          style={{
            color: "#ef4444",
          }}
        >
          Delete
        </span>
      </button>

    </div>,
    document.body
  );
}