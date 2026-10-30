import type { CallDetail, ToolInvocationRow } from "./api";
import { isRecord } from "./guards";

/**
 * One call as a list of things that happened, built from two records of the same events:
 * the transcript ElevenLabs stored (what the agent said and what the platform measured), and
 * `tool_invocations`, our server's own record of each tool request (what we measured).
 *
 * The transcript is passed through from the platform untouched, so nothing in it is trusted:
 * every field may be missing or the wrong type, and this module must never throw on it.
 */

export type MessageItem = {
  kind: "message";
  role: "agent" | "user";
  text: string;
  atSecs: number;
  interrupted: boolean;
  /** Metric name to `elapsed_time`, exactly as the platform reported it. */
  metrics: Record<string, number>;
};

export type ToolItem = {
  kind: "tool";
  name: string;
  requestId: string;
  atSecs: number;
  /** The parsed arguments; the raw text if it was not valid JSON. */
  params: unknown;
  resultText: string | null;
  resultOk: boolean | null;
  /** The platform's view of the round trip: request out to result back. */
  platformMs: number | null;
  /** Our server's view: time spent inside the request, measured by our middleware. */
  serverMs: number | null;
  /** What the platform saw that we did not: network and its own handling. */
  overheadMs: number | null;
  httpStatus: number | null;
};

export type TimelineItem = MessageItem | ToolItem;

export type Timeline = {
  items: TimelineItem[];
  /** Server records with no tool call left to pair with. Normally empty. */
  unmatchedInvocations: ToolInvocationRow[];
};

type ToolResult = { text: string | null; ok: boolean; ms: number | null };

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

type Turn = Record<string, unknown> & { role: "agent" | "user" };

/** A turn without a recognisable role is malformed as a whole; its tool results are ignored too. */
function isTurn(value: unknown): value is Turn {
  return isRecord(value) && (value.role === "agent" || value.role === "user");
}

function recordsIn(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

/**
 * Results are looked up by `request_id` over the whole transcript. A result is not guaranteed to
 * sit in the turn that made the call; in practice it often arrives in a later one.
 */
function collectResults(turns: Turn[]): Map<string, ToolResult> {
  const results = new Map<string, ToolResult>();
  for (const turn of turns) {
    for (const result of recordsIn(turn.tool_results)) {
      const id = result.request_id;
      // An empty id would match every call that has no id of its own.
      if (typeof id !== "string" || id === "" || results.has(id)) continue;
      const seconds = numberOrNull(result.tool_latency_secs);
      results.set(id, {
        text: typeof result.result_value === "string" ? result.result_value : null,
        ok: !result.is_error,
        ms: seconds === null ? null : seconds * 1000,
      });
    }
  }
  return results;
}

function parseParams(raw: unknown): unknown {
  if (typeof raw !== "string") return raw ?? null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    // Show what was sent rather than hide it: a malformed argument is itself a finding.
    return raw;
  }
}

function metricsOf(turn: Turn): Record<string, number> {
  const metrics: Record<string, number> = {};
  const container = turn.conversation_turn_metrics;
  if (!isRecord(container) || !isRecord(container.metrics)) return metrics;
  for (const [name, metric] of Object.entries(container.metrics)) {
    const elapsed = isRecord(metric) ? numberOrNull(metric.elapsed_time) : null;
    if (elapsed !== null) metrics[name] = elapsed;
  }
  return metrics;
}

/** Invocation positions grouped by tool name, each group in the order given (time order). */
function groupByTool(invocations: ToolInvocationRow[]): Map<string, number[]> {
  const groups = new Map<string, number[]>();
  invocations.forEach((invocation, index) => {
    const group = groups.get(invocation.tool);
    if (group) group.push(index);
    else groups.set(invocation.tool, [index]);
  });
  return groups;
}

export function buildTimeline(call: CallDetail): Timeline {
  const invocations = Array.isArray(call.tool_invocations) ? call.tool_invocations.filter(isRecord) : [];
  const turns = (Array.isArray(call.transcript) ? call.transcript : []).filter(isTurn);
  const results = collectResults(turns);

  const byTool = groupByTool(invocations);
  // Pairing is by position within each tool, not within the whole call. Calls to different tools
  // interleave freely, and one tool the platform runs itself (`end_call`) never reaches us, so a
  // global index would shift every later pairing by one.
  const calledSoFar = new Map<string, number>();
  const paired = new Set<number>();

  const items: TimelineItem[] = [];
  for (const turn of turns) {
    const atSecs = numberOrNull(turn.time_in_call_secs) ?? 0;

    if (typeof turn.message === "string" && turn.message.trim() !== "") {
      items.push({
        kind: "message",
        role: turn.role,
        text: turn.message,
        atSecs,
        interrupted: turn.interrupted === true,
        metrics: metricsOf(turn),
      });
    }

    for (const toolCall of recordsIn(turn.tool_calls)) {
      const name = toolCall.tool_name;
      if (typeof name !== "string" || name === "") continue;
      const requestId = typeof toolCall.request_id === "string" ? toolCall.request_id : "";
      const result = results.get(requestId);

      // A call the platform never made has no server record, so it must not use one up.
      let invocation: ToolInvocationRow | undefined;
      if (toolCall.tool_has_been_called === true) {
        const position = calledSoFar.get(name) ?? 0;
        calledSoFar.set(name, position + 1);
        const index = byTool.get(name)?.[position];
        if (index !== undefined) {
          paired.add(index);
          invocation = invocations[index];
        }
      }

      const platformMs = result?.ms ?? null;
      const serverMs = numberOrNull(invocation?.server_ms);
      items.push({
        kind: "tool",
        name,
        requestId,
        atSecs,
        params: parseParams(toolCall.params_as_json),
        resultText: result?.text ?? null,
        resultOk: result?.ok ?? null,
        platformMs,
        serverMs,
        overheadMs: platformMs !== null && serverMs !== null ? platformMs - serverMs : null,
        httpStatus: numberOrNull(invocation?.http_status),
      });
    }
  }

  return { items, unmatchedInvocations: invocations.filter((_, index) => !paired.has(index)) };
}
