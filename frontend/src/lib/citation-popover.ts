// Interaction and placement rules for citation chips, kept free of React so
// they can be tested directly.
//
// A tap on a touch screen also produces emulated hover and focus events; only
// the tap itself may toggle the popover, or it would open and close at once.

export type PopoverState = {
  open: boolean;
  // Opened by a press (tap, click, Enter or Space) rather than by hover or focus.
  pinned: boolean;
};

export type PopoverEvent =
  | { type: "pointer-enter"; pointerType: string }
  | { type: "pointer-leave"; pointerType: string }
  | { type: "press"; pointerType: string }
  | { type: "focus"; keyboard: boolean }
  | { type: "blur" }
  | { type: "key"; key: string }
  | { type: "dismiss" };

export const CLOSED: PopoverState = { open: false, pinned: false };
const PINNED: PopoverState = { open: true, pinned: true };
const PREVIEW: PopoverState = { open: true, pinned: false };


export function nextPopoverState(
  state: PopoverState,
  event: PopoverEvent
): PopoverState {
  switch (event.type) {
    case "pointer-enter":
      return event.pointerType === "mouse" && !state.open
        ? PREVIEW
        : state;

    case "pointer-leave":
      return event.pointerType === "mouse" && !state.pinned
        ? CLOSED
        : state;

    case "press":
      // Mouse: a click pins an open preview; a second click closes it.
      // Touch and pen: each tap toggles.
      return state.pinned
        ? CLOSED
        : PINNED;

    case "focus":
      return event.keyboard && !state.open
        ? PREVIEW
        : state;

    case "key":
      if (event.key === "Escape") {
        return CLOSED;
      }
      return event.key === "Enter" || event.key === " "
        ? (state.pinned ? CLOSED : PINNED)
        : state;

    case "blur":
    case "dismiss":
      return CLOSED;
  }
}


export type Rect = { top: number; bottom: number; left: number; width: number };
export type Size = { width: number; height: number };

export type TipPlacement = {
  left: number;
  top: number;
  placement: "above" | "below";
};

const GAP = 8;


// Above the chip when it fits, otherwise below; always inside the viewport.
export function placeTip(
  chip: Rect,
  tip: Size,
  viewport: Size,
  gutter = 12
): TipPlacement {
  const maxLeft = Math.max(gutter, viewport.width - tip.width - gutter);
  const left = Math.min(
    Math.max(gutter, chip.left + chip.width / 2 - tip.width / 2),
    maxLeft
  );

  const aboveTop = chip.top - GAP - tip.height;
  const belowTop = chip.bottom + GAP;
  const fitsAbove = aboveTop >= gutter;
  const fitsBelow = belowTop + tip.height <= viewport.height - gutter;
  const placement =
    fitsAbove || (!fitsBelow && chip.top > viewport.height - chip.bottom)
      ? "above"
      : "below";

  const maxTop = Math.max(gutter, viewport.height - tip.height - gutter);
  const top = Math.min(
    Math.max(gutter, placement === "above" ? aboveTop : belowTop),
    maxTop
  );

  return { left, top, placement };
}
