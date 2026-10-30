import { ApiError } from "./api";
import { isRecord } from "./guards";

/** What to tell a person when `POST /api/session` fails. */
export function describeSessionError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 0:
        return "Could not reach the backend. Check that it is running, then try again.";
      case 403:
        return "Voice calls can only be started from the computer that runs the backend.";
      case 502:
        return "ElevenLabs did not start the call. Wait a moment and try again.";
      case 503:
        return "Voice is not configured on this server. Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID in .env, then restart the backend.";
    }
  }
  return "Could not start the call. Try again.";
}

/**
 * What to tell a person when the SDK reports a failure. The browser's own error is the only
 * reliable signal for a blocked microphone, so `context` (typed `any` by the SDK) is narrowed here.
 */
export function describeVoiceError(message: string, context: unknown): string {
  const name = isRecord(context) && typeof context.name === "string" ? context.name : "";
  if (name === "NotAllowedError" || /NotAllowedError|permission denied|microphone/i.test(message)) {
    return "The microphone is blocked. Allow microphone access for this site in your browser, then try again.";
  }
  if (name === "NotFoundError") {
    return "No microphone was found. Plug one in or check your sound settings, then try again.";
  }
  return "The call could not be connected. Try again.";
}
