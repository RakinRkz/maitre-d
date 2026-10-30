import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { schedulePostCallRefresh } from "./post-call-refresh";

const timing = { firstAfterMs: 3_000, everyMs: 5_000, forMs: 30_000 };

let client: QueryClient;
let invalidate: MockInstance<QueryClient["invalidateQueries"]>;

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient();
  invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
});

afterEach(() => vi.useRealTimers());

function keysInvalidated(): string[] {
  return invalidate.mock.calls.map(([filters]) => String(filters?.queryKey?.[0]));
}

describe("refreshing after a call ends", () => {
  it("waits for the transcript webhook before the first refresh", () => {
    schedulePostCallRefresh(client, timing);

    vi.advanceTimersByTime(2_999);
    expect(invalidate).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(keysInvalidated()).toEqual(expect.arrayContaining(["bookings", "calls", "messages"]));
  });

  it("repeats every five seconds and then stops, so an open tab does not poll for ever", () => {
    schedulePostCallRefresh(client, timing);
    vi.advanceTimersByTime(timing.firstAfterMs);
    const perRound = invalidate.mock.calls.length;

    vi.advanceTimersByTime(5_000);
    expect(invalidate).toHaveBeenCalledTimes(perRound * 2);

    vi.advanceTimersByTime(timing.forMs * 2);
    const total = invalidate.mock.calls.length;
    vi.advanceTimersByTime(60_000);

    expect(invalidate).toHaveBeenCalledTimes(total);
    // Rounds at 3, 8, 13, 18, 23 and 28 s after the call ended; nothing at or after 30 s.
    expect(total).toBe(perRound * 6);
  });

  it("can be cancelled before it starts and while it runs", () => {
    const cancelEarly = schedulePostCallRefresh(client, timing);
    cancelEarly();
    vi.advanceTimersByTime(60_000);
    expect(invalidate).not.toHaveBeenCalled();

    const cancelLate = schedulePostCallRefresh(client, timing);
    vi.advanceTimersByTime(timing.firstAfterMs);
    const calls = invalidate.mock.calls.length;
    cancelLate();
    vi.advanceTimersByTime(60_000);

    expect(invalidate).toHaveBeenCalledTimes(calls);
  });
});
