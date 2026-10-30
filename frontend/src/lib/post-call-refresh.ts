import type { QueryClient } from "@tanstack/react-query";

const WATCHED = [["bookings"], ["calls"], ["call"], ["messages"], ["tool-latency"]] as const;

export type RefreshTiming = { firstAfterMs: number; everyMs: number; forMs: number };

// The transcript reaches the backend by webhook some seconds after the call ends, so the first
// look is too early. Poll for a while, then stop: an open tab must not poll for ever.
export const DEFAULT_TIMING: RefreshTiming = { firstAfterMs: 3_000, everyMs: 5_000, forMs: 30_000 };

function invalidateAll(client: QueryClient): void {
  for (const queryKey of WATCHED) void client.invalidateQueries({ queryKey });
}

/**
 * Refetch what a finished call changes: once after `firstAfterMs`, then every `everyMs`, with the
 * whole schedule over `forMs` after the call ended. Returns a function that stops it early.
 */
export function schedulePostCallRefresh(
  client: QueryClient,
  timing: RefreshTiming = DEFAULT_TIMING,
): () => void {
  let interval: ReturnType<typeof setInterval> | undefined;

  const first = setTimeout(() => {
    invalidateAll(client);
    interval = setInterval(() => invalidateAll(client), timing.everyMs);
  }, timing.firstAfterMs);

  const cancel = () => {
    clearTimeout(first);
    clearTimeout(stop);
    clearInterval(interval);
  };
  const stop = setTimeout(cancel, timing.forMs);
  return cancel;
}
