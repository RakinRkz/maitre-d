import { lazy, Suspense, useState } from "react";
import { BookingsPanel } from "./components/BookingsPanel";
import { CallDetailView } from "./components/CallDetailView";
import { CallsPanel } from "./components/CallsPanel";
import { LatencyPanel } from "./components/LatencyPanel";
import { MessagesPanel } from "./components/MessagesPanel";

// The voice SDK (and the WebRTC library under it) is most of the bundle. Loading it on its own
// lets the bookings and calls render without waiting for code only the Talk button needs.
const TalkToAgent = lazy(() => import("./components/TalkToAgent").then((module) => ({ default: module.TalkToAgent })));

export function App() {
  // One page, no router: the open call is just a piece of state.
  const [openCall, setOpenCall] = useState<string | null>(null);

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-6">
      <header>
        <h1 className="text-2xl font-bold">Saffron Table</h1>
        <p className="text-muted">Staff dashboard</p>
      </header>
      <main className="space-y-6">
        <Suspense fallback={<p className="text-muted">Loading voice controls…</p>}>
          <TalkToAgent />
        </Suspense>
        <BookingsPanel />
        {openCall === null ? (
          <CallsPanel onOpen={setOpenCall} />
        ) : (
          <CallDetailView conversationId={openCall} onClose={() => setOpenCall(null)} />
        )}
        <MessagesPanel />
        <LatencyPanel />
      </main>
    </div>
  );
}
