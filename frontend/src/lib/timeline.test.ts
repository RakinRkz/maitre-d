import { describe, expect, it } from "vitest";
import type { CallDetail, ToolInvocationRow } from "./api";
import { buildTimeline, type ToolItem } from "./timeline";

// Builders for the platform's transcript shape. Every test states only what it is about.

type Turn = Record<string, unknown>;

function invocation(tool: string, serverMs: number, httpStatus = 200): ToolInvocationRow {
  return { tool, ok: true, error: null, http_status: httpStatus, server_ms: serverMs, created_at: "2026-10-30T12:00:00Z" };
}

/** The transcript's type says objects; the platform's payload is not guaranteed to be, so tests pass `unknown`. */
function callOf(transcript: unknown, invocations: ToolInvocationRow[] = []): CallDetail {
  return {
    conversation_id: "conv_test",
    agent_id: "agent_test",
    started_at: null,
    duration_secs: null,
    call_successful: null,
    summary: null,
    transcript: transcript as CallDetail["transcript"],
    analysis: null,
    metadata: null,
    tool_invocations: invocations,
  };
}

function toolCall(tool: string, requestId: string, params: unknown = {}, called = true): Turn {
  return {
    request_id: requestId,
    tool_name: tool,
    params_as_json: typeof params === "string" ? params : JSON.stringify(params),
    tool_has_been_called: called,
  };
}

function toolResult(tool: string, requestId: string, value: unknown, latencySecs: number, isError = false): Turn {
  return {
    request_id: requestId,
    tool_name: tool,
    result_value: JSON.stringify(value),
    is_error: isError,
    tool_latency_secs: latencySecs,
  };
}

function turn(role: "agent" | "user", message: string | null, extra: Turn = {}): Turn {
  return { role, message, time_in_call_secs: 1, interrupted: false, tool_calls: null, tool_results: null, ...extra };
}

function toolItems(call: CallDetail): ToolItem[] {
  return buildTimeline(call).items.filter((item): item is ToolItem => item.kind === "tool");
}

describe("the order of items", () => {
  it("follows the transcript, with a turn's message before its tool calls", () => {
    // Bug pinned: tool cards listed before the sentence that triggered them.
    const { items } = buildTimeline(
      callOf([
        turn("user", "Table for two, please"),
        turn("agent", "Let me check.", { tool_calls: [toolCall("check_availability", "r1")] }),
        turn("agent", "We have 7pm."),
      ]),
    );

    expect(items.map((item) => (item.kind === "message" ? `${item.role}: ${item.text}` : `tool: ${item.name}`))).toEqual([
      "user: Table for two, please",
      "agent: Let me check.",
      "tool: check_availability",
      "agent: We have 7pm.",
    ]);
  });

  it("yields one tool item per entry when a turn makes several calls", () => {
    const { items } = buildTimeline(
      callOf([turn("agent", null, { tool_calls: [toolCall("a", "r1"), toolCall("b", "r2")] })]),
    );

    expect(items.map((item) => item.kind === "tool" && item.name)).toEqual(["a", "b"]);
  });

  it("yields nothing for a turn that only carries tool results", () => {
    // Bug pinned: an empty bubble for the turn the platform adds to deliver a result.
    const { items } = buildTimeline(
      callOf([turn("agent", null, { tool_results: [toolResult("a", "r1", { ok: true }, 0.1)] })]),
    );

    expect(items).toEqual([]);
  });

  it("treats a whitespace-only message as no message", () => {
    const { items } = buildTimeline(callOf([turn("agent", "   \n")]));

    expect(items).toEqual([]);
  });

  it("carries the time in the call, and reads a missing one as zero", () => {
    const { items } = buildTimeline(callOf([turn("agent", "Hello", { time_in_call_secs: 12.5 }), { role: "user", message: "Hi" }]));

    expect(items.map((item) => item.atSecs)).toEqual([12.5, 0]);
  });
});

describe("message details", () => {
  it("extracts each turn metric's elapsed time by name", () => {
    const { items } = buildTimeline(
      callOf([
        turn("agent", "Hello", {
          conversation_turn_metrics: {
            metrics: { convai_llm_service_ttfb: { elapsed_time: 0.62 }, convai_tts_service_ttfb: { elapsed_time: 0.21 } },
          },
        }),
      ]),
    );

    expect(items[0]).toMatchObject({ metrics: { convai_llm_service_ttfb: 0.62, convai_tts_service_ttfb: 0.21 } });
  });

  it("gives an empty metrics object when the turn has none, or they are malformed", () => {
    const { items } = buildTimeline(
      callOf([
        turn("agent", "No metrics"),
        turn("agent", "Not an object", { conversation_turn_metrics: { metrics: 5 } }),
        turn("agent", "Bad elapsed time", { conversation_turn_metrics: { metrics: { m: { elapsed_time: "fast" }, n: null } } }),
      ]),
    );

    expect(items.map((item) => item.kind === "message" && item.metrics)).toEqual([{}, {}, {}]);
  });

  it("marks only a turn whose interrupted flag is exactly true", () => {
    const { items } = buildTimeline(
      callOf([turn("agent", "a", { interrupted: true }), turn("agent", "b", { interrupted: "yes" }), turn("agent", "c", { interrupted: null })]),
    );

    expect(items.map((item) => item.kind === "message" && item.interrupted)).toEqual([true, false, false]);
  });
});

describe("finding a tool call's result", () => {
  it("reads the result from the same turn", () => {
    const [tool] = toolItems(
      callOf([
        turn("agent", null, {
          tool_calls: [toolCall("check_availability", "r1")],
          tool_results: [toolResult("check_availability", "r1", { ok: true, say: "Yes" }, 0.42)],
        }),
      ]),
    );

    expect(tool).toMatchObject({ resultOk: true, platformMs: 420, resultText: JSON.stringify({ ok: true, say: "Yes" }) });
  });

  it("finds a result that arrives in a later turn, by request id and not by position", () => {
    // Bug pinned: results matched by their position in the transcript. Here the two results come
    // back in the opposite order to the calls, two turns later, so any positional match swaps them.
    const [first, second] = toolItems(
      callOf([
        turn("agent", null, { tool_calls: [toolCall("a", "r1"), toolCall("b", "r2")] }),
        turn("user", "Hello?"),
        turn("agent", null, {
          tool_results: [toolResult("b", "r2", { say: "from b" }, 0.2), toolResult("a", "r1", { say: "from a" }, 0.1)],
        }),
      ]),
    );

    expect(first).toMatchObject({ name: "a", resultText: JSON.stringify({ say: "from a" }), platformMs: 100 });
    expect(second).toMatchObject({ name: "b", resultText: JSON.stringify({ say: "from b" }), platformMs: 200 });
  });

  it("gives null for the result, its status and its latency when no result ever arrives", () => {
    const [tool] = toolItems(callOf([turn("agent", null, { tool_calls: [toolCall("a", "r1")] })]));

    expect(tool).toMatchObject({ resultText: null, resultOk: null, platformMs: null, overheadMs: null });
  });

  it("marks a result the platform flagged as an error as not ok", () => {
    const [tool] = toolItems(
      callOf([
        turn("agent", null, {
          tool_calls: [toolCall("a", "r1")],
          tool_results: [toolResult("a", "r1", { detail: "boom" }, 0.3, true)],
        }),
      ]),
    );

    expect(tool?.resultOk).toBe(false);
  });

  it("converts the platform's seconds to milliseconds", () => {
    // Bug pinned: showing 0.42 as "0 ms".
    const [tool] = toolItems(
      callOf([turn("agent", null, { tool_calls: [toolCall("a", "r1")], tool_results: [toolResult("a", "r1", {}, 0.42)] })]),
    );

    expect(tool?.platformMs).toBe(420);
  });

  it("uses the first result when the platform repeats a request id", () => {
    const [tool] = toolItems(
      callOf([
        turn("agent", null, {
          tool_calls: [toolCall("a", "r1")],
          tool_results: [toolResult("a", "r1", { say: "first" }, 0.1), toolResult("a", "r1", { say: "second" }, 0.9)],
        }),
      ]),
    );

    expect(tool?.resultText).toBe(JSON.stringify({ say: "first" }));
  });

  it("does not attach a result to a call that has no request id", () => {
    const noId = { tool_name: "a", params_as_json: "{}", tool_has_been_called: true };
    const [tool] = toolItems(callOf([turn("agent", null, { tool_calls: [noId], tool_results: [toolResult("a", "", {}, 0.1)] })]));

    expect(tool).toMatchObject({ requestId: "", resultText: null });
  });
});

describe("parameters", () => {
  it("parses the parameters from their JSON text", () => {
    const [tool] = toolItems(callOf([turn("agent", null, { tool_calls: [toolCall("a", "r1", { party_size: 4, name: "Ada" })] })]));

    expect(tool?.params).toEqual({ party_size: 4, name: "Ada" });
  });

  it("keeps invalid JSON as the raw string instead of throwing", () => {
    // Bug pinned: one malformed argument took the whole call-detail page down.
    const [tool] = toolItems(callOf([turn("agent", null, { tool_calls: [toolCall("a", "r1", '{"party_size": 4,')] })]));

    expect(tool?.params).toBe('{"party_size": 4,');
  });

  it("reads a missing parameters field as null", () => {
    const [tool] = toolItems(callOf([turn("agent", null, { tool_calls: [{ tool_name: "a", request_id: "r1" }] })]));

    expect(tool?.params).toBeNull();
  });
});

describe("pairing tool calls with our server's records", () => {
  it("pairs the second create_booking with the second server record", () => {
    // Bug pinned: every call of a tool showing the first record's timing.
    const [first, second] = toolItems(
      callOf(
        [turn("agent", null, { tool_calls: [toolCall("create_booking", "r1"), toolCall("create_booking", "r2")] })],
        [invocation("create_booking", 30, 200), invocation("create_booking", 55, 409)],
      ),
    );

    expect(first).toMatchObject({ serverMs: 30, httpStatus: 200 });
    expect(second).toMatchObject({ serverMs: 55, httpStatus: 409 });
  });

  it("pairs per tool name when different tools are interleaved", () => {
    // Bug pinned: pairing by position in the whole call. Transcript order is check, create, check;
    // server order is check, check, create. A global index would give the create call the second
    // check's record.
    const items = toolItems(
      callOf(
        [
          turn("agent", null, {
            tool_calls: [toolCall("check_availability", "r1"), toolCall("create_booking", "r2"), toolCall("check_availability", "r3")],
          }),
        ],
        [invocation("check_availability", 10), invocation("check_availability", 20), invocation("create_booking", 99)],
      ),
    );

    expect(items.map((item) => item.serverMs)).toEqual([10, 99, 20]);
  });

  it("leaves a tool the platform runs itself unpaired, without shifting the others", () => {
    // Bug pinned: the platform's end_call never reaches our server, and counting it would hand
    // its slot to the next call.
    const [endCall, create] = toolItems(
      callOf(
        [turn("agent", null, { tool_calls: [toolCall("end_call", "r1"), toolCall("create_booking", "r2")] })],
        [invocation("create_booking", 42)],
      ),
    );

    expect(endCall).toMatchObject({ serverMs: null, httpStatus: null, overheadMs: null });
    expect(create?.serverMs).toBe(42);
  });

  it("pairs across turns, taking calls in transcript order", () => {
    const items = toolItems(
      callOf(
        [
          turn("agent", null, { tool_calls: [toolCall("create_booking", "r1")] }),
          turn("user", "Thanks"),
          turn("agent", null, { tool_calls: [toolCall("create_booking", "r2")] }),
        ],
        [invocation("create_booking", 1), invocation("create_booking", 2)],
      ),
    );

    expect(items.map((item) => item.serverMs)).toEqual([1, 2]);
  });

  it("does not let a call the platform never made use up a server record", () => {
    // Bug pinned: ignoring tool_has_been_called, so a call that was prepared but never sent took
    // the record that belongs to the one that was.
    const [skipped, made] = toolItems(
      callOf(
        [turn("agent", null, { tool_calls: [toolCall("create_booking", "r1", {}, false), toolCall("create_booking", "r2", {}, true)] })],
        [invocation("create_booking", 77)],
      ),
    );

    expect(skipped).toMatchObject({ name: "create_booking", serverMs: null, httpStatus: null });
    expect(made?.serverMs).toBe(77);
  });

  it("still lists a call that was not made, and treats a missing flag as not made", () => {
    const missingFlag = { request_id: "r2", tool_name: "create_booking", params_as_json: "{}" };
    const items = toolItems(
      callOf(
        [turn("agent", null, { tool_calls: [toolCall("create_booking", "r1", {}, false), missingFlag] })],
        [invocation("create_booking", 5)],
      ),
    );

    expect(items.map((item) => item.serverMs)).toEqual([null, null]);
    expect(items).toHaveLength(2);
  });

  it("pairs a made call even when its result never arrived", () => {
    const [tool] = toolItems(
      callOf([turn("agent", null, { tool_calls: [toolCall("a", "r1")] })], [invocation("a", 12)]),
    );

    expect(tool).toMatchObject({ serverMs: 12, platformMs: null, overheadMs: null });
  });
});

describe("overhead", () => {
  it("is the platform round trip minus our server's time", () => {
    // Bug pinned: subtracting the other way round, which shows negative overhead.
    const [tool] = toolItems(
      callOf(
        [turn("agent", null, { tool_calls: [toolCall("a", "r1")], tool_results: [toolResult("a", "r1", {}, 0.5)] })],
        [invocation("a", 120)],
      ),
    );

    expect(tool).toMatchObject({ platformMs: 500, serverMs: 120, overheadMs: 380 });
  });

  it("is null when there is no server record to compare with", () => {
    const [tool] = toolItems(
      callOf([turn("agent", null, { tool_calls: [toolCall("end_call", "r1")], tool_results: [toolResult("end_call", "r1", {}, 0.2)] })]),
    );

    expect(tool).toMatchObject({ platformMs: 200, serverMs: null, overheadMs: null });
  });
});

describe("server records with no matching call", () => {
  it("returns the extras, in order, and nothing that was paired", () => {
    const { unmatchedInvocations } = buildTimeline(
      callOf(
        [turn("agent", null, { tool_calls: [toolCall("create_booking", "r1")] })],
        [invocation("create_booking", 1), invocation("find_booking", 2), invocation("create_booking", 3)],
      ),
    );

    expect(unmatchedInvocations.map((row) => [row.tool, row.server_ms])).toEqual([
      ["find_booking", 2],
      ["create_booking", 3],
    ]);
  });

  it("returns nothing when every record was paired", () => {
    const { unmatchedInvocations } = buildTimeline(
      callOf([turn("agent", null, { tool_calls: [toolCall("a", "r1")] })], [invocation("a", 1)]),
    );

    expect(unmatchedInvocations).toEqual([]);
  });

  it("does not pair a record with a call that was never made", () => {
    const { unmatchedInvocations } = buildTimeline(
      callOf([turn("agent", null, { tool_calls: [toolCall("a", "r1", {}, false)] })], [invocation("a", 1)]),
    );

    expect(unmatchedInvocations).toHaveLength(1);
  });
});

describe("odd input", () => {
  it("gives no items and every record unmatched for an empty transcript", () => {
    const records = [invocation("a", 1), invocation("b", 2)];

    expect(buildTimeline(callOf([], records))).toEqual({ items: [], unmatchedInvocations: records });
  });

  it("gives an empty timeline when there is nothing at all", () => {
    expect(buildTimeline(callOf([]))).toEqual({ items: [], unmatchedInvocations: [] });
  });

  it("skips entries that are not turns, without throwing", () => {
    const transcript = [null, 7, "text", [], undefined, { message: "no role" }, { role: "system", message: "wrong role" }, turn("agent", "Real")];

    const { items } = buildTimeline(callOf(transcript));

    expect(items.map((item) => item.kind === "message" && item.text)).toEqual(["Real"]);
  });

  it("ignores a turn with no role as a whole, results included", () => {
    // Bug pinned: accepting any object as a turn, so a half-formed one produced a card.
    const roleless = { tool_calls: [toolCall("a", "r1")], tool_results: [toolResult("a", "r1", {}, 0.1)] };
    const real = turn("agent", null, { tool_calls: [toolCall("b", "r2")] });

    const items = toolItems(callOf([roleless, real, { role: "agent", tool_results: [toolResult("b", "r2", {}, 0.2)] }]));

    expect(items.map((item) => item.name)).toEqual(["b"]);
    expect(items[0]?.platformMs).toBe(200);
  });

  it("copes with tool calls and results that are not arrays, or hold junk", () => {
    const transcript = [
      turn("agent", "one", { tool_calls: "nope", tool_results: 5 }),
      turn("agent", "two", { tool_calls: { a: 1 }, tool_results: {} }),
      turn("agent", "three", { tool_calls: [null, 3, "x", [], {}, { tool_name: 5 }, { tool_name: "" }], tool_results: [null, 1, {}] }),
    ];

    const { items } = buildTimeline(callOf(transcript));

    expect(items.map((item) => item.kind)).toEqual(["message", "message", "message"]);
  });

  it("skips a tool call that has no name, since nothing can be paired or shown for it", () => {
    const items = toolItems(callOf([turn("agent", null, { tool_calls: [{ request_id: "r1", tool_has_been_called: true }, toolCall("a", "r2")] })]));

    expect(items.map((item) => item.name)).toEqual(["a"]);
  });

  it("reads a latency that is missing or not a number as no latency", () => {
    const result = { request_id: "r1", result_value: "{}", is_error: false, tool_latency_secs: "slow" };
    const [tool] = toolItems(callOf([turn("agent", null, { tool_calls: [toolCall("a", "r1")], tool_results: [result] })]));

    expect(tool).toMatchObject({ resultOk: true, platformMs: null });
  });

  it("gives an empty timeline when the transcript or the records are not arrays at all", () => {
    const call = callOf("not a transcript");
    call.tool_invocations = null as unknown as ToolInvocationRow[];

    expect(buildTimeline(call)).toEqual({ items: [], unmatchedInvocations: [] });
  });
});
