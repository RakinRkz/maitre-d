import type { UseQueryResult } from "@tanstack/react-query";
import { useId, type ReactNode } from "react";

export const buttonClass =
  "rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-neutral-bg disabled:cursor-not-allowed disabled:opacity-60";

export function Panel(props: { title: string; actions?: ReactNode; children: ReactNode }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="rounded-lg border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id={headingId} className="text-lg font-semibold">
          {props.title}
        </h2>
        {props.actions}
      </div>
      {props.children}
    </section>
  );
}

/**
 * Loading, error and empty states for one query, so every panel handles all three the same way.
 * An error after a successful load keeps the old rows on screen beneath the message, rather than
 * blanking the panel because one refresh failed.
 */
export function QueryView<T>(props: {
  query: UseQueryResult<T>;
  isEmpty: (data: T) => boolean;
  empty: string;
  children: (data: T) => ReactNode;
}) {
  const { query } = props;
  if (query.isPending) {
    return (
      <p role="status" className="text-muted">
        Loading…
      </p>
    );
  }
  return (
    <>
      {query.isError && (
        <div role="alert" className="mb-3 flex flex-wrap items-center gap-3 rounded-md bg-bad-bg p-3 text-bad">
          <span>{query.error.message}</span>
          <button type="button" className={buttonClass} onClick={() => void query.refetch()}>
            Try again
          </button>
        </div>
      )}
      {query.data !== undefined &&
        (props.isEmpty(query.data) ? <p className="text-muted">{props.empty}</p> : props.children(query.data))}
    </>
  );
}

type Tone = "ok" | "bad" | "warn" | "neutral";

const toneClass: Record<Tone, string> = {
  ok: "bg-ok-bg text-ok",
  bad: "bg-bad-bg text-bad",
  warn: "bg-warn-bg text-warn",
  neutral: "bg-neutral-bg text-muted",
};

// `inline-block` keeps a struck-through table row from striking the badge too: text decoration
// does not propagate into inline-block children.
export function Badge(props: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${toneClass[props.tone]}`}>
      {props.children}
    </span>
  );
}

export function OutcomeBadge(props: { value: string | null }) {
  if (props.value === "success") return <Badge tone="ok">Success</Badge>;
  if (props.value === "failure") return <Badge tone="bad">Failed</Badge>;
  return <Badge tone="neutral">Unknown</Badge>;
}

/** Tables scroll sideways on a phone rather than squash; the region is focusable so keys can scroll it. */
export function TableWrap(props: { label: string; children: ReactNode }) {
  return (
    <div role="region" aria-label={props.label} tabIndex={0} className="overflow-x-auto">
      <table className="w-full min-w-max text-left text-sm">
        <caption className="sr-only">{props.label}</caption>
        {props.children}
      </table>
    </div>
  );
}

export const headCell = "border-b border-line px-3 py-2 font-medium text-muted";
export const cell = "border-b border-line px-3 py-2 align-top";
