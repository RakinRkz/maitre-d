import { formatDateTime, formatDuration } from "../lib/format";
import { useCalls } from "../lib/queries";
import { OutcomeBadge, Panel, QueryView } from "./ui";

export function CallsPanel(props: { onOpen: (conversationId: string) => void }) {
  const calls = useCalls();
  return (
    <Panel title="Recent calls">
      <QueryView query={calls} isEmpty={(rows) => rows.length === 0} empty="No calls yet.">
        {(rows) => (
          <ul className="divide-y divide-line">
            {rows.map((call) => (
              <li key={call.conversation_id}>
                <button
                  type="button"
                  onClick={() => props.onOpen(call.conversation_id)}
                  className="w-full rounded-md px-2 py-3 text-left hover:bg-neutral-bg"
                >
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="font-medium">{formatDateTime(call.started_at)}</span>
                    <span className="text-sm text-muted">{formatDuration(call.duration_secs)}</span>
                    <OutcomeBadge value={call.call_successful} />
                  </span>
                  <span className="mt-1 block text-sm text-muted">{call.summary ?? "No summary yet."}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </QueryView>
    </Panel>
  );
}
