"use client";

import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";


type Anchor = {
  x: number;
  y: number;
  above: boolean;
};

const VIEWPORT_GUTTER = 12;


// Short source label inside an answer; the full reference opens on hover, focus or tap.
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

  const tipId =
    useId();

  const [
    anchor,
    setAnchor,
  ] = useState<Anchor | null>(
    null
  );


  function show() {
    const rect =
      chipRef.current
        ?.getBoundingClientRect();

    if (!rect) {
      return;
    }

    const above =
      rect.top > 120;

    setAnchor({
      x: rect.left + rect.width / 2,
      y: above
        ? rect.top - 8
        : rect.bottom + 8,
      above,
    });
  }


  function hide() {
    setAnchor(null);
  }


  // Centre on the chip, then clamp to the viewport once the width is known.
  useLayoutEffect(() => {
    const tip =
      tipRef.current;

    if (
      !anchor
      || !tip
    ) {
      return;
    }

    const width =
      tip.offsetWidth;

    const left =
      Math.min(
        Math.max(
          VIEWPORT_GUTTER,
          anchor.x - width / 2
        ),
        window.innerWidth - width - VIEWPORT_GUTTER
      );

    tip.style.left = `${left}px`;
  }, [
    anchor,
  ]);


  useEffect(() => {
    if (!anchor) {
      return;
    }

    window.addEventListener(
      "scroll",
      hide,
      true
    );

    window.addEventListener(
      "resize",
      hide
    );

    return () => {
      window.removeEventListener(
        "scroll",
        hide,
        true
      );

      window.removeEventListener(
        "resize",
        hide
      );
    };
  }, [
    anchor,
  ]);


  return (
    <span
      ref={chipRef}
      tabIndex={0}
      aria-describedby={
        anchor
          ? tipId
          : undefined
      }
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onClick={() => {
        if (anchor) {
          hide();
        } else {
          show();
        }
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          hide();
        }
      }}
      className="aura-cite"
    >
      <span className="sr-only">
        Source:
        {" "}
      </span>

      {label}

      {anchor && (
        <span
          ref={tipRef}
          id={tipId}
          role="tooltip"
          data-placement={
            anchor.above
              ? "above"
              : "below"
          }
          style={{
            top: anchor.y,
            left: anchor.x,
          }}
          className="aura-cite-tip"
        >
          {full}
        </span>
      )}
    </span>
  );
}
