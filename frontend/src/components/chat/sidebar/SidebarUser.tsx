"use client";

import {
  Check,
  Laptop,
  LogOut,
  Moon,
  Settings,
  Sun,
} from "lucide-react";

import {
  useEffect,
  useRef,
  useState,
} from "react";

import {
  User,
} from "@/types/chat";


type ThemeMode =
  | "system"
  | "light"
  | "dark";


type Props = {
  user: User | null;

  collapsed?: boolean;

  onLogout: () => void;
};


export default function SidebarUser({
  user,
  collapsed = false,
  onLogout,
}: Props) {
  const [
    settingsOpen,
    setSettingsOpen,
  ] = useState(false);


  const [
    themeMode,
    setThemeMode,
  ] = useState<ThemeMode>(
    "system"
  );


  const settingsRef =
    useRef<HTMLDivElement | null>(
      null
    );


  useEffect(() => {
    const savedTheme =
      localStorage.getItem(
        "theme"
      );


    const initialMode:
      ThemeMode =
        savedTheme === "light"
          ? "light"
          : savedTheme === "dark"
            ? "dark"
            : "system";


    const timeoutId = window.setTimeout(() => {
      setThemeMode(initialMode);
    }, 0);

    const systemDark = window.matchMedia(
      "(prefers-color-scheme: dark)"
    ).matches;

    document.documentElement.classList.toggle(
      "dark",
      initialMode === "dark"
      || (initialMode === "system" && systemDark)
    );

    return () => window.clearTimeout(timeoutId);
  }, []);


  useEffect(() => {
    const mediaQuery =
      window.matchMedia(
        "(prefers-color-scheme: dark)"
      );


    function handleSystemThemeChange() {
      if (
        themeMode !== "system"
      ) {
        return;
      }

      document
        .documentElement
        .classList
        .toggle(
          "dark",
          mediaQuery.matches
        );
    }


    mediaQuery.addEventListener(
      "change",
      handleSystemThemeChange
    );


    return () => {
      mediaQuery.removeEventListener(
        "change",
        handleSystemThemeChange
      );
    };
  }, [
    themeMode,
  ]);


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


  function applyTheme(
    mode: ThemeMode
  ) {
    const systemDark =
      window.matchMedia(
        "(prefers-color-scheme: dark)"
      ).matches;


    const shouldUseDark =
      mode === "dark"
      || (
        mode === "system"
        && systemDark
      );


    document
      .documentElement
      .classList
      .toggle(
        "dark",
        shouldUseDark
      );
  }


  function changeTheme(
    mode: ThemeMode
  ) {
    setThemeMode(
      mode
    );


    if (
      mode === "system"
    ) {
      localStorage.setItem(
        "theme",
        "system"
      );

    } else {
      localStorage.setItem(
        "theme",
        mode
      );
    }


    applyTheme(
      mode
    );
  }


  if (collapsed) {
    return (
      <div
        className="
          border-t
          border-[var(--border)]
          px-2
          py-3
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
              mx-auto
              h-9
              w-9
              rounded-full
              object-cover
            "
          />
        ) : (
          <div
            className="
              mx-auto
              flex
              h-9
              w-9
              items-center
              justify-center
              rounded-full
              bg-[var(--primary)]
              text-xs
              font-medium
              text-white
            "
          >
            {user?.name
              ?.charAt(0)
              .toUpperCase()
              || "U"}
          </div>
        )}
      </div>
    );
  }


  return (
    <div
      className="
        relative
        border-t
        border-[var(--border)]
        px-3
        py-3
      "
    >
      <div
        className="
          flex
          items-center
          gap-2
          rounded-xl
          px-2
          py-2
          transition
          hover:bg-[var(--surface-hover)]
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
              h-8
              w-8
              shrink-0
              rounded-full
              object-cover
            "
          />
        ) : (
          <div
            className="
              flex
              h-8
              w-8
              shrink-0
              items-center
              justify-center
              rounded-full
              bg-[var(--primary)]
              text-xs
              font-medium
              text-white
            "
          >
            {user?.name
              ?.charAt(0)
              .toUpperCase()
              || "U"}
          </div>
        )}


        <span
          className="
            min-w-0
            flex-1
            truncate
            text-sm
            font-medium
            text-[var(--text-primary)]
          "
        >
          {user?.name
            || "User"}
        </span>


        <div
          ref={
            settingsRef
          }
          className="
            relative
            shrink-0
          "
        >
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
              h-8
              w-8
              items-center
              justify-center
              rounded-lg
              transition-all
              duration-150
              active:scale-95

              ${
                settingsOpen
                  ? "bg-[var(--surface-active)] text-[var(--text-primary)]"
                  : "text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
              }
            `}
          >
            <Settings
              size={17}
              strokeWidth={1.8}
            />
          </button>


          {settingsOpen && (
            <div
              className="
                absolute
                bottom-11
                right-0
                z-50
                w-56
                overflow-hidden
                rounded-2xl
                border
                border-[var(--border)]
                bg-[var(--menu)]
                p-1.5
                text-[var(--text-primary)]
                shadow-xl
                shadow-black/30
              "
            >
              <div
                className="
                  px-3
                  pb-1
                  pt-2
                "
              >
                <p
                  className="
                    text-[10px]
                    font-semibold
                    uppercase
                    tracking-[0.08em]
                    text-[var(--text-muted)]
                  "
                >
                  Appearance
                </p>
              </div>


              <ThemeOption
                icon={
                  <Laptop
                    size={16}
                  />
                }

                label="System"

                active={
                  themeMode === "system"
                }

                onClick={() =>
                  changeTheme(
                    "system"
                  )
                }
              />


              <ThemeOption
                icon={
                  <Sun
                    size={16}
                  />
                }

                label="Light"

                active={
                  themeMode === "light"
                }

                onClick={() =>
                  changeTheme(
                    "light"
                  )
                }
              />


              <ThemeOption
                icon={
                  <Moon
                    size={16}
                  />
                }

                label="Dark"

                active={
                  themeMode === "dark"
                }

                onClick={() =>
                  changeTheme(
                    "dark"
                  )
                }
              />


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
                  setSettingsOpen(
                    false
                  );

                  onLogout();
                }}
                className="
                  flex
                  w-full
                  items-center
                  gap-2.5
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
                <LogOut
                  size={16}
                  strokeWidth={1.8}
                  className="
                    shrink-0
                    text-red-500
                  "
                />

                <span
                  className="
                    flex-1
                    text-red-500
                  "
                >
                  Log out
                </span>
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


function ThemeOption({
  icon,
  label,
  active,
  onClick,
}: {
  icon: React.ReactNode;

  label: string;

  active: boolean;

  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={
        onClick
      }
      className={`
        flex
        w-full
        items-center
        gap-2.5
        rounded-xl
        px-3
        py-2.5
        text-left
        text-sm
        transition

        ${
          active
            ? "bg-[var(--surface-active)] text-[var(--text-primary)]"
            : "text-[var(--text-secondary)] hover:bg-[var(--menu-hover)] hover:text-[var(--text-primary)]"
        }
      `}
    >
      <span
        className="
          shrink-0
        "
      >
        {icon}
      </span>


      <span
        className="
          min-w-0
          flex-1
        "
      >
        {label}
      </span>


      {active && (
        <Check
          size={15}
          className="
            shrink-0
            text-[var(--text-primary)]
          "
        />
      )}
    </button>
  );
}