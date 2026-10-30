import { describe, expect, it } from "vitest";
import { describeResult } from "./tool-result";

describe("showing a tool's reply", () => {
  it("shows the spoken sentence when the reply has a say field", () => {
    expect(describeResult(JSON.stringify({ ok: true, say: "You are booked for seven." }))).toEqual({
      text: "You are booked for seven.",
      spoken: true,
      refusal: null,
    });
  });

  it("names the reason when the tool refused", () => {
    const reply = JSON.stringify({ ok: false, error: "slot_taken", say: "Sorry, that time is gone." });

    expect(describeResult(reply)).toMatchObject({ spoken: true, refusal: "slot_taken" });
  });

  it("shows the raw text when it is JSON without a say field", () => {
    expect(describeResult('{"detail":"boom"}')).toEqual({ text: '{"detail":"boom"}', spoken: false, refusal: null });
  });

  it("shows the raw text when it is not JSON at all", () => {
    expect(describeResult("Internal Server Error")).toEqual({
      text: "Internal Server Error",
      spoken: false,
      refusal: null,
    });
  });

  it("shows the raw text when say is not a string", () => {
    expect(describeResult('{"say": 5}')).toMatchObject({ spoken: false, text: '{"say": 5}' });
  });

  it("shows nothing when there is no result", () => {
    expect(describeResult(null)).toBeNull();
  });
});
