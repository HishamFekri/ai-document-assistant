// Server timestamps are canonical UTC instants; they are displayed in the
// device's own time zone and locale. Nothing here changes a stored value, and
// the device clock is only used to decide labels like "Yesterday", never to
// order anything.

const EXPLICIT_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i;


// The API serializes UTC timestamps without an offset (e.g. "2026-10-26T13:05:00");
// JavaScript would read those as device-local time, so mark them as UTC.
export function parseServerTimestamp(
  value: string | null | undefined
): Date | null {
  const text = value?.trim();

  if (!text) {
    return null;
  }

  const date = new Date(
    EXPLICIT_OFFSET.test(text)
      ? text
      : `${text}Z`
  );

  return Number.isNaN(date.getTime())
    ? null
    : date;
}


// Milliseconds since the epoch, for ordering; NaN when unparseable.
export function timestampValue(
  value: string | null | undefined
): number {
  return parseServerTimestamp(value)?.getTime() ?? Number.NaN;
}


export type DisplayZone = {
  timeZone?: string;
  locale?: string;
};


function dayKey(
  date: Date,
  zone: DisplayZone
) {
  // en-CA formats as YYYY-MM-DD, a stable calendar-day key in any time zone.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: zone.timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}


// Calendar days between two instants as seen in the display time zone
// (0 = same day, 1 = the previous day). DST-safe: compares calendar dates.
export function calendarDaysBetween(
  earlier: Date,
  later: Date,
  zone: DisplayZone = {}
) {
  const [a, b] = [dayKey(earlier, zone), dayKey(later, zone)].map((key) => {
    const [year, month, day] = key.split("-").map(Number);
    return Date.UTC(year, month - 1, day);
  });

  return Math.round((b - a) / 86_400_000);
}


// "14:05" today, "Yesterday, 14:05", "Oct 24, 14:05" this year, otherwise with the year.
export function formatMessageTimestamp(
  value: string | null | undefined,
  now: Date = new Date(),
  zone: DisplayZone = {}
): string {
  const date = parseServerTimestamp(value);

  if (!date) {
    return "";
  }

  const time = new Intl.DateTimeFormat(zone.locale, {
    timeZone: zone.timeZone,
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);

  const days = calendarDaysBetween(date, now, zone);

  if (days === 0 || days < 0) {
    return time;
  }

  if (days === 1) {
    const yesterday = new Intl.RelativeTimeFormat(zone.locale, { numeric: "auto" }).format(-1, "day");
    return `${yesterday.charAt(0).toLocaleUpperCase(zone.locale)}${yesterday.slice(1)}, ${time}`;
  }

  const sameYear =
    new Intl.DateTimeFormat("en-CA", { timeZone: zone.timeZone, year: "numeric" }).format(date)
    === new Intl.DateTimeFormat("en-CA", { timeZone: zone.timeZone, year: "numeric" }).format(now);

  return new Intl.DateTimeFormat(zone.locale, {
    timeZone: zone.timeZone,
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}


// Full, unambiguous form for a tooltip.
export function formatFullTimestamp(
  value: string | null | undefined,
  zone: DisplayZone = {}
): string {
  const date = parseServerTimestamp(value);

  return date
    ? new Intl.DateTimeFormat(zone.locale, {
        timeZone: zone.timeZone,
        dateStyle: "full",
        timeStyle: "short",
      }).format(date)
    : "";
}


// ---- Display clock: re-render when the time zone or the calendar day changes ----

type Listener = () => void;

const listeners = new Set<Listener>();
let snapshot = "";
let timer: number | null = null;


function currentSnapshot() {
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return `${timeZone}|${dayKey(new Date(), { timeZone })}`;
}


function check() {
  const next = currentSnapshot();

  if (next !== snapshot) {
    snapshot = next;
    listeners.forEach((listener) => listener());
  }
}


export function subscribeDisplayClock(
  listener: Listener
) {
  listeners.add(listener);

  if (listeners.size === 1) {
    snapshot = currentSnapshot();
    // A changed device time zone or a passing midnight is picked up within a
    // minute, and immediately when the tab regains focus.
    timer = window.setInterval(check, 60_000);
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
  }

  return () => {
    listeners.delete(listener);

    if (listeners.size === 0 && timer !== null) {
      window.clearInterval(timer);
      timer = null;
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    }
  };
}


export function getDisplayClockSnapshot() {
  if (!snapshot) {
    snapshot = currentSnapshot();
  }

  return snapshot;
}


// Server render has no device clock; timestamps are rendered after hydration.
export function getServerDisplayClockSnapshot() {
  return "";
}
