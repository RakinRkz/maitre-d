// Staff read London time whatever their laptop's zone says, so every time here is shown in it.
const LONDON = "Europe/London";

const dateTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: LONDON,
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDateTime(iso: string | null): string {
  if (iso === null) return "Unknown";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "Unknown" : dateTime.format(date);
}

/** 83 becomes "1m 23s". A call's length, so seconds are the useful unit. */
export function formatDuration(secs: number | null): string {
  if (secs === null) return "Unknown";
  const minutes = Math.floor(secs / 60);
  return minutes > 0 ? `${minutes}m ${secs % 60}s` : `${secs}s`;
}

/** Position in a call, as m:ss. */
export function formatClock(secs: number): string {
  const whole = Math.max(0, Math.floor(secs));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** Milliseconds to the nearest whole one; a single digit after the point is false precision. */
export function formatMs(ms: number | null): string {
  return ms === null ? "n/a" : `${Math.round(ms)} ms`;
}
