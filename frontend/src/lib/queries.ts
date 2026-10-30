import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

// Prefixes are what `post-call-refresh.ts` invalidates, so the two files must agree on them.
export function useBookings(date: string) {
  return useQuery({
    // An empty date means "today", which the server works out; the key keeps it separate.
    queryKey: ["bookings", date],
    queryFn: ({ signal }) => api.bookings(date === "" ? undefined : date, signal),
  });
}

export function useCalls() {
  return useQuery({ queryKey: ["calls"], queryFn: ({ signal }) => api.calls(signal) });
}

export function useCall(conversationId: string) {
  return useQuery({
    queryKey: ["call", conversationId],
    queryFn: ({ signal }) => api.call(conversationId, signal),
  });
}

export function useMessages() {
  return useQuery({ queryKey: ["messages"], queryFn: ({ signal }) => api.messages(signal) });
}

export function useToolLatency() {
  return useQuery({ queryKey: ["tool-latency"], queryFn: ({ signal }) => api.toolLatency(signal) });
}
