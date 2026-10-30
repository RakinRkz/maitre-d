import { useEffect, useMemo, useRef } from "react";
import type { CallDetail } from "../lib/api";
import { formatDateTime, formatDuration } from "../lib/format";
import { useCall } from "../lib/queries";
import { buildTimeline } from "../lib/timeline";
import { TimelineView } from "./TimelineView";
import { buttonClass, OutcomeBadge, Panel, QueryView } from "./ui";

export function CallDetailView(props: { conversationId: string; onClose: () => void }) {
  const call = useCall(props.conversationId);
  const closeRef = useRef<HTMLButtonElement>(null);

  // The list this replaced held the focus; put it on the way back so a keyboard user is not lost.
  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  return (
    <Panel
      title="Call timeline"
      actions={
        <button ref={closeRef} type="button" className={buttonClass} onClick={props.onClose}>
          Back to calls
        </button>
      }
    >
      <QueryView query={call} isEmpty={() => false} empty="">
        {(detail) => <Detail detail={detail} />}
      </QueryView>
    </Panel>
  );
}

function Detail({ detail }: { detail: CallDetail }) {
  const timeline = useMemo(() => buildTimeline(detail), [detail]);
  return (
    <>
      <div className="mb-4 space-y-1">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-medium">{formatDateTime(detail.started_at)}</span>
          <span className="text-sm text-muted">{formatDuration(detail.duration_secs)}</span>
          <OutcomeBadge value={detail.call_successful} />
        </p>
        <p className="text-sm text-muted">{detail.summary ?? "No summary yet."}</p>
      </div>
      <TimelineView timeline={timeline} />
    </>
  );
}
