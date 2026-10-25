// The App-level hydration helper: the reload path reads the composer's session facts
// (model / permission mode) from the session record the list already holds,
// instead of leaving the app defaults on screen until the WS `ready` frame lands.

import { describe, expect, it } from "vitest";
import { sessionComposerState, sessionRemoteWorkspace } from "./appHelpers";

describe("sessionComposerState", () => {
  it("reads the record's model and mode", () => {
    expect(sessionComposerState({ model: "opencode-go:qwen3.7-max", mode: "plan" })).toEqual({
      model: "opencode-go:qwen3.7-max",
      mode: "plan",
    });
  });

  it("drops empty fields — an unrecorded fact must not overwrite a live one with ''", () => {
    expect(sessionComposerState({ model: "", mode: "auto" })).toEqual({
      mode: "auto",
    });
  });

  it("handles a missing record", () => {
    expect(sessionComposerState(null)).toEqual({});
    expect(sessionComposerState(undefined)).toEqual({});
  });
});

describe("sessionRemoteWorkspace", () => {
  it("reads the explicit server field when present", () => {
    expect(
      sessionRemoteWorkspace({
        remote_workspace: "/home/ubuntu",
        environment_id: "h1",
        workspace: "C:\\Users\\DELI",
      }),
    ).toBe("/home/ubuntu");
  });

  it("falls back to a bound pre-field row's workspace (which IS the host's path)", () => {
    expect(
      sessionRemoteWorkspace({
        environment_id: "h1",
        workspace: "/home/ubuntu",
      }),
    ).toBe("/home/ubuntu");
  });

  it("answers null for a local session or a missing record", () => {
    expect(sessionRemoteWorkspace({ environment_id: null, workspace: "C:\\ws" })).toBeNull();
    expect(sessionRemoteWorkspace({ workspace: "C:\\ws" })).toBeNull();
    expect(sessionRemoteWorkspace(null)).toBeNull();
    expect(sessionRemoteWorkspace(undefined)).toBeNull();
  });
});
