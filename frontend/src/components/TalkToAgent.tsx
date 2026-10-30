import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { schedulePostCallRefresh } from "../lib/post-call-refresh";
import { describeSessionError, describeVoiceError } from "../lib/voice-errors";
import { buttonClass, Panel } from "./ui";

// The only file that imports the voice SDK. The SDK's hooks need a provider above them.
export function TalkToAgent() {
  return (
    <ConversationProvider>
      <Talk />
    </ConversationProvider>
  );
}

// What this component knows that the SDK does not: we are fetching a token, we are ending the
// call, or the last attempt failed. Connecting, connected and speaking come from the SDK.
type Phase =
  | { kind: "idle" }
  | { kind: "requesting" }
  | { kind: "ending" }
  | { kind: "failed"; message: string };

function Talk() {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const session = useMutation({ mutationFn: api.startSession });

  // Only a call that connected can have produced a transcript, so only that one refreshes the data.
  const connected = useRef(false);
  const cancelRefresh = useRef<(() => void) | null>(null);
  useEffect(() => () => cancelRefresh.current?.(), []);

  const { startSession, endSession, status, isSpeaking } = useConversation({
    onConnect: () => {
      connected.current = true;
    },
    onDisconnect: (details) => {
      if (connected.current) {
        connected.current = false;
        cancelRefresh.current?.();
        cancelRefresh.current = schedulePostCallRefresh(queryClient);
      }
      setPhase((current) => {
        if (current.kind === "failed") return current; // keep the more specific message
        return details.reason === "error"
          ? { kind: "failed", message: describeVoiceError(details.message, details.context) }
          : { kind: "idle" };
      });
    },
    onError: (message, context) => {
      setPhase({ kind: "failed", message: describeVoiceError(message, context) });
    },
  });

  function start() {
    setPhase({ kind: "requesting" });
    session.mutate(undefined, {
      onSuccess: (issued) => {
        // The SDK takes over from here: its status moves to "connecting" in this same tick.
        setPhase({ kind: "idle" });
        startSession({
          conversationToken: issued.token,
          connectionType: "webrtc",
          ...(issued.environment !== null && { environment: issued.environment }),
        });
      },
      onError: (error) => setPhase({ kind: "failed", message: describeSessionError(error) }),
    });
  }

  function end() {
    setPhase({ kind: "ending" });
    endSession();
  }

  const view =
    phase.kind === "failed" || phase.kind === "ending"
      ? phase.kind
      : phase.kind === "requesting" || status === "connecting"
        ? "connecting"
        : status === "connected"
          ? "connected"
          : "idle";

  return (
    <Panel title="Talk to the agent">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {view === "connected" || view === "ending" ? (
          <button type="button" className={buttonClass} disabled={view === "ending"} onClick={end}>
            {view === "ending" ? "Ending…" : "End call"}
          </button>
        ) : (
          <button
            type="button"
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-fg disabled:cursor-not-allowed disabled:opacity-60"
            disabled={view === "connecting"}
            onClick={start}
          >
            {view === "connecting" ? "Connecting…" : view === "failed" ? "Try again" : "Talk to the agent"}
          </button>
        )}
        <p role="status" className="text-sm">
          {view === "connecting" && "Connecting. Your browser may ask to use the microphone."}
          {view === "connected" && (isSpeaking ? "Connected. The agent is speaking." : "Connected. The agent is listening.")}
          {view === "ending" && "Ending the call."}
        </p>
      </div>
      {phase.kind === "failed" && (
        <p role="alert" className="mt-3 rounded-md bg-bad-bg p-3 text-sm text-bad">
          {phase.message}
        </p>
      )}
      <p className="mt-3 text-sm text-muted">Calls are time-limited.</p>
    </Panel>
  );
}
