// Settings → Appearance: the mode control keeps writing the theme key; the Theme picker
// lists the sixteen palettes in their three groups with Reeve picked by default, moves with radio
// semantics across groups, and writes the html attribute and `reeve:palette`; the font pickers
// draw each face in itself, and picking one ends the theme's pair.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AppearanceSettings } from "./AppearanceSettings";

const VARS = ["--sans", "--mono", "--term-font", "--fs-body", "--code-delta", "--code-liga"];
const css = (name: string) => document.documentElement.style.getPropertyValue(name);

function reset() {
  localStorage.clear();
  delete document.documentElement.dataset.palette;
  delete document.documentElement.dataset.theme;
  for (const name of VARS) document.documentElement.style.removeProperty(name);
}

beforeEach(reset);
afterEach(() => {
  cleanup();
  reset();
});

describe("Appearance → Theme", () => {
  it("lists sixteen palettes in three groups, Reeve picked by default", () => {
    render(<AppearanceSettings />);
    const group = screen.getByRole("radiogroup", { name: "Theme" });
    const names = within(group)
      .getAllByRole("radio")
      .map((o) => o.getAttribute("aria-label"));
    expect(names).toHaveLength(16);
    expect(names.slice(0, 3)).toEqual(["Reeve", "Harbor", "One"]);
    expect(names).toEqual(expect.arrayContaining(["Catppuccin", "Rosé Pine", "Nord", "Gruvbox", "Solarized", "Tokyo Night", "GitHub"]));
    expect(screen.getByText("Editor classics")).toBeTruthy();
    expect(screen.getByTestId("palette-reeve").getAttribute("aria-checked")).toBe("true");
  });

  it("picking a palette sets data-palette and persists it; Reeve clears the key, Harbor the attribute", () => {
    render(<AppearanceSettings />);
    fireEvent.click(screen.getByTestId("palette-nord"));
    expect(document.documentElement.dataset.palette).toBe("nord");
    expect(localStorage.getItem("reeve:palette")).toBe("nord");
    expect(screen.getByTestId("palette-nord").getAttribute("aria-checked")).toBe("true");

    fireEvent.click(screen.getByTestId("palette-harbor"));
    expect(document.documentElement.dataset.palette).toBeUndefined();
    expect(localStorage.getItem("reeve:palette")).toBe("harbor");

    fireEvent.click(screen.getByTestId("palette-reeve"));
    expect(document.documentElement.dataset.palette).toBe("reeve");
    expect(localStorage.getItem("reeve:palette")).toBeNull();
  });

  it("arrow keys move across the groups in order", () => {
    render(<AppearanceSettings />);
    fireEvent.click(screen.getByTestId("palette-harbor"));
    fireEvent.keyDown(screen.getByTestId("palette-harbor"), { key: "ArrowRight" });
    expect(document.documentElement.dataset.palette).toBe("one"); // first of the next group
    fireEvent.keyDown(screen.getByTestId("palette-one"), { key: "End" });
    expect(screen.getByTestId("palette-linear").getAttribute("aria-checked")).toBe("true");
  });

  it("keeps Light / Dark / Auto working", () => {
    render(<AppearanceSettings />);
    const mode = screen.getByRole("radiogroup", { name: "Appearance" });
    fireEvent.click(within(mode).getByRole("radio", { name: "Dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("reeve:theme")).toBe("dark");
  });
});

describe("Appearance → Fonts", () => {
  it("shows the theme's pair while the switch is on, each face drawn in itself", () => {
    render(<AppearanceSettings />);
    expect(screen.getByTestId("theme-fonts").getAttribute("aria-checked")).toBe("true");
    const ui = screen.getByTestId("font-picker-ui");
    expect(ui.textContent).toContain("Manrope");
    fireEvent.click(ui);
    const plex = screen.getByTestId("font-ui-ibm-plex-sans");
    expect((plex.querySelector(".settings-select-option-label") as HTMLElement).style.fontFamily).toContain("IBM Plex Sans Variable");
  });

  it("picking a face ends the theme's pair and applies the face", () => {
    render(<AppearanceSettings />);
    fireEvent.click(screen.getByTestId("font-picker-ui"));
    fireEvent.click(screen.getByTestId("font-ui-source-sans-3"));
    expect(css("--sans")).toContain("Source Sans 3 Variable");
    expect(screen.getByTestId("theme-fonts").getAttribute("aria-checked")).toBe("false");
    expect(localStorage.getItem("reeve:font-ui")).toBe("source-sans-3");
  });

  it("the terminal says it follows the code face, and can take its own", () => {
    render(<AppearanceSettings />);
    expect(screen.getByTestId("font-picker-terminal").textContent).toContain("Same as code font");
    fireEvent.click(screen.getByTestId("font-picker-terminal"));
    fireEvent.click(screen.getByTestId("font-terminal-victor-mono"));
    expect(css("--term-font")).toContain("Victor Mono Variable");
    expect(localStorage.getItem("reeve:font-terminal")).toBe("victor-mono");
  });

  it("sizes and ligatures apply at once", () => {
    render(<AppearanceSettings />);
    fireEvent.click(screen.getByTestId("text-size-large"));
    expect(css("--fs-body")).toBe("15px");
    fireEvent.click(screen.getByTestId("code-size-small"));
    expect(css("--code-delta")).toBe("-1px");
    fireEvent.click(screen.getByTestId("code-ligatures"));
    expect(css("--code-liga")).toBe("normal");
  });
});
