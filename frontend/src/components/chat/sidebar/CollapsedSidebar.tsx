"use client";

import {
  LogOut,
  MessageSquarePlus,
  PanelLeftOpen,
  Settings,
} from "lucide-react";

import {
  useEffect,
  useRef,
  useState,
} from "react";

import ThemeToggle from "@/components/theme/ThemeToggle";

import {
  User,
} from "@/types/chat";


type Props = {
  user: User | null;

  creatingChat: boolean;

  onOpenSidebar: () => void;

  onCreateChat: () => void;

  onLogout: () => void;
};


export default function CollapsedSidebar({
  user,
  creatingChat,
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


  useEffect(() => {
    function handleOutsideClick(
      event: MouseEvent
    ) {
      if (
        settingsRef.current
        && !settingsRef.current.contains(
          event.target as Node
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
      className="
        flex
        h-screen
        w-[64px]
        shrink-0
        flex-col
        border-r
        border-[var(--border)]
        bg-[var(--sidebar)]
        text-[var(--text-primary)]
      "
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
          {settingsOpen && (
            <div
              className="
                absolute
                bottom-0
                left-[52px]
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
            </div>
          )}


          <button
            type="button"
            onClick={() =>
              setSettingsOpen(
                (
                  current
                ) => !current
              )
            }
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