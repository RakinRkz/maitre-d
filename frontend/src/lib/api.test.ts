import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

function reply(status: number, body: unknown, statusText = ""): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return new Response(text, { status, statusText });
}

// A fresh Response per call: a body can be read only once.
function stubFetch(response: Response) {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(() => Promise.resolve(response.clone()));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("the API helper", () => {
  it("throws an error carrying the backend's detail and the status", async () => {
    stubFetch(reply(503, { detail: "Voice is not configured" }));

    const failure = await api.startSession().catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ message: "Voice is not configured", status: 503 });
  });

  it("falls back to the status line when the body is not JSON", async () => {
    // A proxy's HTML error page must not become the message.
    stubFetch(reply(502, "<html>Bad gateway</html>", "Bad Gateway"));

    await expect(api.calls()).rejects.toThrow("502 Bad Gateway");
  });

  it("does not show a validation error's list of details as a message", async () => {
    stubFetch(reply(422, { detail: [{ msg: "bad" }] }, "Unprocessable Entity"));

    await expect(api.bookings("not-a-date")).rejects.toThrow("422 Unprocessable Entity");
  });

  it("reports an unreachable backend as status zero", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(api.calls()).rejects.toMatchObject({ status: 0 });
  });

  it("omits the date parameter when none is given, so the server picks today", async () => {
    const fetchMock = stubFetch(reply(200, []));

    await api.bookings(undefined);
    await api.bookings("2026-10-31");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(["/api/bookings", "/api/bookings?date=2026-10-31"]);
  });

  it("escapes a conversation id before putting it in the path", async () => {
    const fetchMock = stubFetch(reply(200, {}));

    await api.call("conv/../x y");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/calls/conv%2F..%2Fx%20y");
  });

  it("starts a session with a POST", async () => {
    const fetchMock = stubFetch(reply(200, { token: "t", conversation_id: "c", environment: null }));

    await expect(api.startSession()).resolves.toEqual({ token: "t", conversation_id: "c", environment: null });

    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });
  });
});
