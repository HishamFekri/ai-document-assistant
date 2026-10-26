// Plans how list rows should animate after an update (FLIP: compare where each
// row was with where it is now). Pure, so the decisions are testable without a DOM.

export type ListMotionPlan = {
  // Rows that changed position: animate from their old offset back to zero.
  moved: { id: string; fromOffset: number }[];
  // Rows that were not in the list before: fade in.
  entered: string[];
};


export function planListMotion(
  previous: ReadonlyMap<string, number>,
  current: ReadonlyMap<string, number>
): ListMotionPlan {
  // First render (or a list that was empty): nothing moves into place.
  if (previous.size === 0) {
    return { moved: [], entered: [] };
  }

  const moved: ListMotionPlan["moved"] = [];
  const entered: string[] = [];

  for (const [id, top] of current) {
    const before = previous.get(id);

    if (before === undefined) {
      entered.push(id);
    } else if (Math.abs(before - top) >= 1) {
      moved.push({ id, fromOffset: before - top });
    }
  }

  return { moved, entered };
}
