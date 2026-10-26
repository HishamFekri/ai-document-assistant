"use client";

import {
  type RefObject,
  useLayoutEffect,
  useRef,
} from "react";

import { planListMotion } from "@/lib/list-motion";


const EASE = "cubic-bezier(0.2, 0.9, 0.2, 1)";


// Smoothly moves rows marked with data-motion-id when their order changes, and
// fades in rows that appear. Uses transform and opacity only (no layout work)
// and does nothing for people who prefer reduced motion.
export function useListMotion(
  containerRef: RefObject<HTMLElement | null>,
  signature: string
) {
  const positions =
    useRef<Map<string, number>>(
      new Map()
    );

  useLayoutEffect(() => {
    const container = containerRef.current;

    if (!container) {
      return;
    }

    const rows = new Map<string, HTMLElement>();
    const current = new Map<string, number>();

    for (const element of container.querySelectorAll<HTMLElement>("[data-motion-id]")) {
      const id = element.dataset.motionId;

      if (id) {
        rows.set(id, element);
        // offsetTop ignores running transforms, so interrupted moves stay accurate.
        current.set(id, element.offsetTop);
      }
    }

    const plan = planListMotion(positions.current, current);
    positions.current = current;

    if (
      typeof window.matchMedia !== "function"
      || window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }

    for (const { id, fromOffset } of plan.moved) {
      rows.get(id)?.animate(
        [{ transform: `translateY(${fromOffset}px)` }, { transform: "translateY(0)" }],
        { duration: 260, easing: EASE }
      );
    }

    for (const id of plan.entered) {
      rows.get(id)?.animate(
        [{ opacity: 0, transform: "translateY(-4px)" }, { opacity: 1, transform: "translateY(0)" }],
        { duration: 220, easing: EASE }
      );
    }
  }, [
    containerRef,
    signature,
  ]);
}
