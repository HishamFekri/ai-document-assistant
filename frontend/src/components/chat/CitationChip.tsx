"use client";

import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import {
  CLOSED,
  nextPopoverState,
  placeTip,
  type PopoverEvent,
} from "@/lib/citation-popover";


// Short source label inside an answer. Mouse: hover previews, click pins.
// Touch: a tap opens and a second tap (or a tap elsewhere) closes.
// Keyboard: focus previews, Enter or Space pins, Escape closes.
export default function CitationChip({
  label,
  full,
}: {
  label: string;
  full: string;
}) {
  const chipRef =
    useRef<HTMLSpanElement | null>(
      null
    );

  const tipRef =
    useRef<HTMLSpanElement | null>(
      null
    );

  // Pointer type of the press in progress; click events do not carry it everywhere.
  const pressPointer =
    useRef("mouse");

  const tipId =
    useId();

  const [
    state,
    setState,
  ] = useState(CLOSED);


  function send(event: PopoverEvent) {
    setState((current) => nextPopoverState(current, event));
  }


  // Measure first, then place inside the viewport.
  useLayoutEffect(() => {
    const chip = chipRef.current;
    const tip = tipRef.current;

    if (!state.open || !chip || !tip) {
      return;
    }

    const placed = placeTip(
      chip.getBoundingClientRect(),
      { width: tip.offsetWidth, height: tip.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight }
    );

    tip.style.left = `${placed.left}px`;
    tip.style.top = `${placed.top}px`;
    tip.dataset.placement = placed.placement;
    tip.style.visibility = "visible";
  }, [
    state.open,
  ]);


  useEffect(() => {
    if (!state.open) {
      return;
    }

    const dismiss = () => setState(CLOSED);

    const outside = (event: PointerEvent) => {
      if (!chipRef.current?.contains(event.target as Node)) {
        dismiss();
      }
    };

    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    document.addEventListener("pointerdown", outside);

    return () => {
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
      document.removeEventListener("pointerdown", outside);
    };
  }, [
    state.open,
  ]);


  return (
    <span
      ref={chipRef}
      role="button"
      tabIndex={0}
      aria-expanded={state.open}
      aria-describedby={
        state.open
          ? tipId
          : undefined
      }
      onPointerDown={(event) => {
        pressPointer.current = event.pointerType || "mouse";
      }}
      onPointerEnter={(event) => {
        send({ type: "pointer-enter", pointerType: event.pointerType });
      }}
      onPointerLeave={(event) => {
        send({ type: "pointer-leave", pointerType: event.pointerType });
      }}
      onClick={() => {
        send({ type: "press", pointerType: pressPointer.current });
      }}
      onFocus={(event) => {
        send({ type: "focus", keyboard: event.currentTarget.matches(":focus-visible") });
      }}
      onBlur={() => {
        send({ type: "blur" });
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " " || event.key === "Escape") {
          event.preventDefault();
          send({ type: "key", key: event.key });
        }
      }}
      className="aura-cite"
    >
      <span className="sr-only">
        Source:
        {" "}
      </span>

      {label}

      {state.open && (
        <span
          ref={tipRef}
          id={tipId}
          role="tooltip"
          style={{
            left: 0,
            top: 0,
            visibility: "hidden",
          }}
          className="aura-cite-tip"
        >
          {full}
        </span>
      )}
    </span>
  );
}
