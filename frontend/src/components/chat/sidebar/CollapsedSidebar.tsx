"use client";

import {
  LogOut,
  MessageSquarePlus,
  PanelLeftOpen,
  Settings,
} from "lucide-react";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  createPortal,
} from "react-dom";

import ThemeToggle from "@/components/theme/ThemeToggle";

import {
  User,
} from "@/types/chat";


type Props = {
  user: User | null;

  creatingChat: boolean;

  // Animate in from the full width (only right after the user collapses it).
  animate?: boolean;

  onOpenSidebar: () => void;

  onCreateChat: () => void;

  onLogout: () => void;
};


export default function CollapsedSidebar({
  user,
  creatingChat,
  animate = false,
  onOpenSidebar,
  onCreateChat,
  onLogout,
}: Props) {
  const [
    settingsOpen,
    setSettingsOpen,
  ] = useState(false);


  const settingsRef =
    useRef<HTMLDivElement | null>(
      null
    );


  const menuRef =
    useRef<HTMLDivElement | null>(
      null
    );


  // The rail clips its content (overflow-hidden, needed for the rounded
  // panel and the collapse animation), so the menu is portaled to <body>
  // and placed beside the button: 52px from its left edge, bottom-aligned.
  const [
    menuPosition,
    setMenuPosition,
  ] = useState({
    left: 0,
    bottom: 0,
  });


  const updateMenuPosition =
    useCallback(() => {
      const rect =
        settingsRef.current
          ?.getBoundingClientRect();

      if (!rect) {
        return;
      }

      setMenuPosition({
        left:
          rect.left
          + 52,

        bottom:
          window.innerHeight
          - rect.bottom,
      });
    }, []);


  useEffect(() => {
    if (!settingsOpen) {
      return;
    }

    window.addEventListener(
      "resize",
      updateMenuPosition
    );

    return () => {
      window.removeEventListener(
        "resize",
        updateMenuPosition
      );
    };
  }, [
    settingsOpen,
    updateMenuPosition,
  ]);


  useEffect(() => {
    function handleOutsideClick(
      event: MouseEvent
    ) {
      const target =
        event.target as Node;

      if (
        settingsRef.current
        && !settingsRef.current.contains(
          target
        )
        && !menuRef.current?.contains(
          target
        )
      ) {
        setSettingsOpen(
          false
        );
      }
    }

    document.addEventListener(
      "mousedown",
      handleOutsideClick
    );

    return () => {
      document.removeEventListener(
        "mousedown",
        handleOutsideClick
      );
    };
  }, []);


  const userInitial =
    user?.name
      ?.trim()
      ?.charAt(0)
      ?.toUpperCase()
    || "U";


  return (
    <aside
      className={`
        ${animate ? "aura-sidebar-collapse" : ""}
        my-2
        ml-2
        flex
        h-[calc(100dvh-1rem)]
        w-[64px]
        shrink-0
        flex-col
        overflow-hidden
        rounded-[18px]
        border
        border-[var(--border-soft)]
        bg-[var(--sidebar)]
        text-[var(--text-primary)]
      `}
    >
      <div
        className="
          flex
          flex-col
          items-center
          gap-1
          px-2
          pt-3
        "
      >
        <button
          type="button"
          onClick={
            onOpenSidebar
          }
          title="Open sidebar"
          className="
            flex
            h-10
            w-10
            items-center
            justify-center
            rounded-lg
            text-[var(--text-secondary)]
            transition-all
            duration-150
            hover:bg-[var(--surface-hover)]
            hover:text-[var(--text-primary)]
            active:scale-95
          "
        >
          <PanelLeftOpen
            size={20}
            strokeWidth={1.8}
          />
        </button>


        <button
          type="button"
          onClick={
            onCreateChat
          }
          disabled={
            creatingChat
          }
          title="New chat"
          className="
            flex
            h-10
            w-10
            items-center
            justify-center
            rounded-lg
            text-[var(--text-primary)]
            transition-all
            duration-150
            hover:bg-[var(--surface-hover)]
            active:scale-95
            disabled:cursor-not-allowed
            disabled:opacity-40
          "
        >
          <MessageSquarePlus
            size={20}
            strokeWidth={1.8}
          />
        </button>
      </div>


      <div className="flex-1" />


      <div
        className="
          flex
          flex-col
          items-center
          gap-2
          border-t
          border-[var(--border)]
          px-2
          py-3
        "
      >
        <div
          ref={
            settingsRef
          }
          className="
            relative
          "
        >
          {settingsOpen && createPortal(
            <div
              ref={
                menuRef
              }
              style={{
                left:
                  menuPosition.left,

                bottom:
                  menuPosition.bottom,
              }}
              className="
                aura
                fixed
                z-50
                w-[210px]
                rounded-xl
                border
                border-[var(--border)]
                bg-[var(--menu)]
                p-1.5
                shadow-xl
                shadow-black/20
              "
            >
              <div
                className="
                  flex
                  items-center
                  justify-between
                  rounded-lg
                  px-3
                  py-2
                  text-sm
                  text-[var(--text-primary)]
                "
              >
                <span>
                  Appearance
                </span>

                <ThemeToggle />
              </div>


              <div
                className="
                  my-1
                  border-t
                  border-[var(--border-soft)]
                "
              />


              <button
                type="button"
                onClick={() => {
                  setSettingsOpen(
                    false
                  );

                  onLogout();
                }}
                className="
                  flex
                  w-full
                  items-center
                  gap-3
                  rounded-lg
                  px-3
                  py-2.5
                  text-left
                  text-sm
                  font-medium
                  text-red-500
                  transition-colors
                  duration-150
                  hover:bg-red-500/10
                "
              >
                <LogOut
                  size={17}
                  strokeWidth={1.8}
                  className="
                    text-red-500
                  "
                />

                <span
                  className="
                    text-red-500
                  "
                >
                  Log out
                </span>
              </button>
            </div>,
            document.body
          )}


          <button
            type="button"
            onClick={() => {
              updateMenuPosition();

              setSettingsOpen(
                (
                  current
                ) => !current
              );
            }}
            title="Settings"
            className={`
              flex
              h-10
              w-10
              items-center
              justify-center
              rounded-lg
              transition-all
              duration-150
              active:scale-95

              ${
                settingsOpen
                  ? "bg-[var(--surface-hover)] text-[var(--text-primary)]"
                  : "text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
              }
            `}
          >
            <Settings
              size={20}
              strokeWidth={1.8}
            />
          </button>
        </div>


        <div
          title={
            user?.name
            || "User"
          }
          className="
            flex
            h-9
            w-9
            items-center
            justify-center
            overflow-hidden
            rounded-full
            border
            border-[var(--border)]
            bg-[var(--surface)]
          "
        >
          {user?.picture ? (
            <img
              src={
                user.picture
              }
              alt={
                user.name
                || "Profile"
              }
              className="
                h-full
                w-full
                object-cover
              "
            />
          ) : (
            <span
              className="
                text-xs
                font-semibold
                text-[var(--text-primary)]
              "
            >
              {userInitial}
            </span>
          )}
        </div>
      </div>
    </aside>
  );
}