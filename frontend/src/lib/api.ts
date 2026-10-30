import type { components } from "./api-schema";

// Aliases for the generated types, not copies: the shapes come from the backend's OpenAPI document.
type Schemas = components["schemas"];
export type BookingRow = Schemas["BookingRow"];
export type CallRow = Schemas["CallRow"];
export type CallDetail = Schemas["CallDetail"];
export type MessageRow = Schemas["MessageRow"];
export type ToolLatencyRow = Schemas["ToolLatencyRow"];
export type ToolInvocationRow = Schemas["ToolInvocationRow"];
export type SessionOut = Schemas["SessionOut"];

/** A non-2xx reply. The message is the backend's `detail`, so it is safe to show. */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      // FastAPI sends a list here for a 422; only a plain sentence is fit to show.
      if (typeof body.detail === "string") return body.detail;
    }
  } catch {
    // Not JSON (a proxy error page, say); fall through to the status line.
  }
  return `${response.status} ${response.statusText}`.trim();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    // An aborted request is the caller's doing (TanStack cancels stale queries); don't relabel it.
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Could not reach the server. Is the backend running?", 0);
  }
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  // The generated types are the contract; nothing here re-validates the body at runtime.
  return (await response.json()) as T;
}

export const api = {
  /** Omit `date` to get today in London, as the server defines it. */
  bookings: (date: string | undefined, signal?: AbortSignal) =>
    request<BookingRow[]>(date ? `/api/bookings?${new URLSearchParams({ date })}` : "/api/bookings", { signal }),
  calls: (signal?: AbortSignal) => request<CallRow[]>("/api/calls", { signal }),
  call: (conversationId: string, signal?: AbortSignal) =>
    request<CallDetail>(`/api/calls/${encodeURIComponent(conversationId)}`, { signal }),
  messages: (signal?: AbortSignal) => request<MessageRow[]>("/api/messages", { signal }),
  toolLatency: (signal?: AbortSignal) => request<ToolLatencyRow[]>("/api/metrics/tool-latency", { signal }),
  startSession: () => request<SessionOut>("/api/session", { method: "POST" }),
};
