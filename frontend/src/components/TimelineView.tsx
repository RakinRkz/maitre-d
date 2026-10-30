import { formatClock, formatMs } from "../lib/format";
import { isRecord } from "../lib/guards";
import type { MessageItem, Timeline, ToolItem } from "../lib/timeline";
import { describeResult } from "../lib/tool-result";
import { Badge } from "./ui";

export function TimelineView(props: { timeline: Timeline }) {
  const { items, unmatchedInvocations } = props.timeline;
  return (
    <>
      {items.length === 0 ? (
        <p className="text-muted">No transcript has been recorded for this call.</p>
      ) : (
        <ol className="space-y-3">
          {items.map((item, index) => (
            // The list is rebuilt whole from the stored transcript and never reordered, so the
            // position is a stable key; nothing else on an item is unique.
            <li key={index}>{item.kind === "message" ? <Bubble item={item} /> : <ToolCard item={item} />}</li>
          ))}
        </ol>
      )}
      {unmatchedInvocations.length > 0 && (
        <details className="mt-4 text-sm text-muted">
          <summary>
            {unmatchedInvocations.length} server record{unmatchedInvocations.length === 1 ? "" : "s"} could not be matched
            to a tool call in the transcript
          </summary>
          <ul className="mt-2 list-disc pl-5">
            {unmatchedInvocations.map((invocation, index) => (
              <li key={index}>
                <span className="font-mono">{invocation.tool}</span>, {formatMs(invocation.server_ms)}, HTTP{" "}
                {invocation.http_status}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function Bubble({ item }: { item: MessageItem }) {
  const fromAgent = item.role === "agent";
  const metrics = Object.entries(item.metrics);
  return (
    <div className={`flex ${fromAgent ? "justify-start" : "justify-end"}`}>
      <div
        className={`max-w-[85%] rounded-lg border px-3 py-2 ${
          fromAgent ? "border-line bg-neutral-bg" : "border-accent bg-surface"
        }`}
      >
        <p className="text-xs text-muted">
          {fromAgent ? "Agent" : "Caller"} at {formatClock(item.atSecs)}
          {item.interrupted && " (interrupted)"}
        </p>
        <p className="whitespace-pre-wrap">{item.text}</p>
        {metrics.length > 0 && (
          <p className="mt-1 text-xs text-muted">
            {metrics.map(([name, seconds]) => `${name} ${seconds.toFixed(2)} s`).join(" · ")}
          </p>
        )}
      </div>
    </div>
  );
}

function ToolCard({ item }: { item: ToolItem }) {
  const result = describeResult(item.resultText);
  return (
    <div className="rounded-lg border border-dashed border-line px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono font-semibold">{item.name}</span>
        {item.resultOk === null ? (
          <Badge tone="neutral">No result</Badge>
        ) : item.resultOk ? (
          <Badge tone="ok">Returned</Badge>
        ) : (
          <Badge tone="bad">Error</Badge>
        )}
        <span className="text-xs text-muted">at {formatClock(item.atSecs)}</span>
      </div>

      <Params value={item.params} />

      {result !== null && (
        <p className="mt-2">
          <span className="text-muted">{result.spoken ? "Agent was told to say: " : "Reply: "}</span>
          <span className={result.spoken ? "" : "font-mono break-all"}>{result.text}</span>
          {result.refusal !== null && <span className="text-muted"> (refused: {result.refusal})</span>}
        </p>
      )}

      <dl className="mt-2 grid gap-2 sm:grid-cols-3">
        <Figure label="Platform round trip" value={formatMs(item.platformMs)} />
        <Figure label="Our server" value={item.serverMs === null ? "no server record" : formatMs(item.serverMs)} />
        {item.overheadMs !== null && <Figure label="Network and platform overhead" value={formatMs(item.overheadMs)} />}
      </dl>
      {item.httpStatus !== null && <p className="mt-1 text-xs text-muted">Our server answered HTTP {item.httpStatus}.</p>}
    </div>
  );
}

function Figure(props: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{props.label}</dt>
      <dd className="font-medium">{props.value}</dd>
    </div>
  );
}

function show(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function Params({ value }: { value: unknown }) {
  if (isRecord(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return <p className="mt-2 text-muted">No parameters.</p>;
    return (
      <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5">
        {entries.map(([key, entry]) => (
          <div key={key} className="contents">
            <dt className="text-muted">{key}</dt>
            <dd className="break-words">{show(entry)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  if (value === null || value === "") return <p className="mt-2 text-muted">No parameters.</p>;
  // Not an object: invalid JSON kept as text, or some other JSON value.
  return <p className="mt-2 font-mono break-all">{show(value)}</p>;
}
