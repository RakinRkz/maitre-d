import { isRecord } from "./guards";

export type ResultView = {
  /** The sentence the agent was told to speak, or the raw reply when there is none. */
  text: string;
  spoken: boolean;
  /** Our tools answer 200 for business outcomes; this is the reason when `ok` was false. */
  refusal: string | null;
};

/**
 * Our tools reply `{ok, error, say, ...}` and the platform stores that as text. The caller only
 * ever heard `say`, so that is what the timeline shows; anything else is shown as it came.
 */
export function describeResult(resultText: string | null): ResultView | null {
  if (resultText === null) return null;
  let reply: unknown;
  try {
    reply = JSON.parse(resultText);
  } catch {
    return { text: resultText, spoken: false, refusal: null };
  }
  if (!isRecord(reply) || typeof reply.say !== "string") {
    return { text: resultText, spoken: false, refusal: null };
  }
  const refusal = reply.ok === false && typeof reply.error === "string" ? reply.error : null;
  return { text: reply.say, spoken: true, refusal };
}
