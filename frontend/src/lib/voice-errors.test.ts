import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { describeSessionError, describeVoiceError } from "./voice-errors";

describe("explaining a failed session request", () => {
  it.each([
    [503, /not configured/i],
    [403, /computer that runs the backend/i],
    [502, /ElevenLabs/],
    [0, /backend/i],
  ])("gives status %i its own actionable message", (status, pattern) => {
    expect(describeSessionError(new ApiError("detail from the server", status))).toMatch(pattern);
  });

  it("never passes an unexpected server message through to the person", () => {
    expect(describeSessionError(new ApiError("Traceback (most recent call last)", 500))).toBe(
      "Could not start the call. Try again.",
    );
    expect(describeSessionError(new Error("anything"))).toBe("Could not start the call. Try again.");
  });
});

describe("explaining a failed voice connection", () => {
  it("recognises a blocked microphone from the browser's error name", () => {
    expect(describeVoiceError("Session failed to start", { name: "NotAllowedError" })).toMatch(/microphone is blocked/);
  });

  it("recognises a blocked microphone from the message when the error is not passed on", () => {
    expect(describeVoiceError("NotAllowedError: Permission denied", undefined)).toMatch(/microphone is blocked/);
  });

  it("recognises a missing microphone", () => {
    expect(describeVoiceError("failed", { name: "NotFoundError" })).toMatch(/No microphone/);
  });

  it("falls back to a general message for anything else", () => {
    expect(describeVoiceError("room closed", "not an object")).toBe("The call could not be connected. Try again.");
  });
});
