// How the project-memory submenu shortens paths and what it lists.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ProjectBindMenu, trimPath } from "./ProjectBindMenu";

const MENU = {
  kind: "memory",
  bound: null,
  derived: { kind: "folder", label: "~/a/b/c/d/notes", full: "/u/a/b/c/d/notes", key: "/u/a/b/c/d/notes" },
  named: [
    { name: "reeve", key: "/k1" },
    { name: "personal-ops", key: "/k2" },
  ],
};

const serve = (payload: unknown) => vi.stubGlobal("fetch", vi.fn(async () => ({ json: async () => payload }) as Response));

beforeEach(() => serve(MENU));

describe("trimPath", () => {
  it("leaves a path of three parts or fewer as it is", () => {
    expect(trimPath("~/projects/api")).toBe("~/projects/api");
  });
  it("keeps only the last three parts of a longer one", () => {
    expect(trimPath("~/fleet/ro4d/demo-universe/notes")).toBe("…/ro4d/demo-universe/notes");
  });
});

describe("ProjectBindMenu", () => {
  it("lists this folder's own memory, shortened, then the named ones, with no filter for so few", async () => {
    render(<ProjectBindMenu sessionId="s1" kind="memory" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("reeve")).toBeTruthy());
    expect(screen.getByText("…/c/d/notes")).toBeTruthy();
    expect(screen.getByText("this folder")).toBeTruthy();
    expect(screen.queryByPlaceholderText("Filter…")).toBeNull();
    expect(screen.getByText(/Name current memory/)).toBeTruthy();
  });

  it("from six named memories there is a filter, and only five are listed before filtering", async () => {
    serve({ ...MENU, named: Array.from({ length: 7 }, (_, i) => ({ name: `mem-${i}`, key: `/k${i}` })) });
    render(<ProjectBindMenu sessionId="s1" kind="memory" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByPlaceholderText("Filter…")).toBeTruthy());
    expect(screen.getByText("mem-4")).toBeTruthy();
    expect(screen.queryByText("mem-5")).toBeNull();
  });
});
