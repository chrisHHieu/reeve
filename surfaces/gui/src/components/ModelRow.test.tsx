// @vitest-environment jsdom
// One model in Settings (ModelRow.tsx): a model its provider marks deprecated says so beside its
// name, as the composer's picker does, so it is not turned on or made the default unawares.

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ModelRow } from "./ModelRow";
import type { CatalogModel } from "../api";

afterEach(cleanup);

const row = (over: Partial<CatalogModel> = {}): CatalogModel => ({
  id: "opencode-go:space-bunny",
  name: "Space Bunny",
  provider: "opencode-go",
  context: 200_000,
  ...over,
});

const draw = (model: CatalogModel) =>
  render(<ModelRow id={model.id} name={model.name} row={model} on={false} isDefault={false} onToggle={() => {}} />);

describe("ModelRow", () => {
  it("tags a model its provider marks deprecated", () => {
    draw(row({ status: "deprecated" }));
    const tag = screen.getByTestId("model-retiring-opencode-go:space-bunny");
    expect(tag.textContent).toBe("Retiring");
    expect(screen.getByTestId("model-setting-opencode-go:space-bunny").contains(tag)).toBe(true);
  });

  it("says nothing of a model in service, or one in another status", () => {
    draw(row());
    draw(row({ id: "opencode-go:step-5-preview", status: "beta" }));
    expect(screen.queryByText("Retiring")).toBeNull();
  });
});
