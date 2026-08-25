"use client";

import {
  Moon,
  Sun,
} from "lucide-react";

import {
  useEffect,
  useState,
} from "react";


type Theme =
  | "light"
  | "dark";


export default function ThemeToggle() {
  const [
    theme,
    setTheme,
  ] = useState<Theme>(() => {
    if (typeof window === "undefined") {
      return "light";
    }

    const savedTheme = localStorage.getItem("theme");

    if (savedTheme === "dark" || savedTheme === "light") {
      return savedTheme;
    }

    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  });


  useEffect(() => {
    const savedTheme =
      localStorage.getItem(
        "theme"
      );


    const systemDark =
      window.matchMedia(
        "(prefers-color-scheme: dark)"
      ).matches;


    const initialTheme:
      Theme =
        savedTheme === "dark"
          ? "dark"
          : savedTheme === "light"
            ? "light"
            : systemDark
              ? "dark"
              : "light";


    document
      .documentElement
      .classList
      .toggle(
        "dark",
        initialTheme === "dark"
      );


  }, []);


  function toggleTheme() {
    const nextTheme:
      Theme =
        theme === "dark"
          ? "light"
          : "dark";


    document
      .documentElement
      .classList
      .toggle(
        "dark",
        nextTheme === "dark"
      );


    localStorage.setItem(
      "theme",
      nextTheme
    );


    setTheme(
      nextTheme
    );
  }


  return (
    <button
      type="button"
      onClick={
        toggleTheme
      }
      title={
        theme === "dark"
          ? "Light mode"
          : "Dark mode"
      }
      className="
        flex
        h-9
        w-9
        shrink-0
        items-center
        justify-center
        rounded-lg
        transition
        hover:bg-neutral-200/60
      "
    >
      {theme === "dark" ? (
        <Sun
          size={19}
          strokeWidth={1.8}
        />
      ) : (
        <Moon
          size={19}
          strokeWidth={1.8}
        />
      )}
    </button>
  );
}