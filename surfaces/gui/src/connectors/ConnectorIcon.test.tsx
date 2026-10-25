import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ConnectorBadge, ConnectorIcon, hexToRgba, isDarkMark } from "./ConnectorIcon";

afterEach(cleanup);

const mark = (container: HTMLElement, selector = "[data-logo]") => container.querySelector(selector) as HTMLElement;

describe("ConnectorIcon", () => {
  it("draws a known logo from the registry in the colour it is given", () => {
    const { container } = render(<ConnectorIcon connector={{ logo: "slack", brand_color: "#611f69" }} />);
    expect(mark(container).getAttribute("data-logo")).toBe("slack");
    expect(mark(container).querySelector("svg")).not.toBeNull();
    expect(mark(container).style.getPropertyValue("--brand")).toBe("#611f69");
  });

  it("an id the registry does not know is the plug, still in the colour given", () => {
    const { container } = render(<ConnectorIcon connector={{ logo: "does-not-exist", brand_color: "#123456" }} />);
    expect(mark(container).getAttribute("data-logo")).toBe("fallback");
    expect(mark(container).querySelector("svg")).not.toBeNull();
    expect(mark(container).style.getPropertyValue("--brand")).toBe("#123456");
  });

  it("with no colour at all it is grey", () => {
    const { container } = render(<ConnectorIcon connector={{ logo: "" }} />);
    expect(mark(container).getAttribute("data-logo")).toBe("fallback");
    expect(mark(container).style.getPropertyValue("--brand")).toBe("#6b7280");
  });

  it("marks a near-black logo so the dark theme can lift it", () => {
    expect(isDarkMark("#1f2328")).toBe(true); // GitHub
    expect(isDarkMark("#ff7a59")).toBe(false);
    expect(isDarkMark("not-a-color")).toBe(false);
    const dark = render(<ConnectorIcon connector={{ logo: "github", brand_color: "#1f2328" }} />);
    expect(mark(dark.container, ".connector-icon").getAttribute("data-dark-mark")).toBe("true");
    const bright = render(<ConnectorIcon connector={{ logo: "figma", brand_color: "#f24e1e" }} />);
    expect(mark(bright.container, ".connector-icon").hasAttribute("data-dark-mark")).toBe(false);
  });

  it("reads three-digit and six-digit colours, and nothing else", () => {
    expect(hexToRgba("#abc", 0.5)).toBe("rgba(170, 187, 204, 0.5)");
    expect(hexToRgba("611f69", 0.12)).toBe("rgba(97, 31, 105, 0.12)");
    expect(hexToRgba("red", 1)).toBe("");
  });
});

describe("ConnectorBadge", () => {
  it("is the registry's logo on a square of the connector's colour", () => {
    const { container } = render(<ConnectorBadge connector={{ logo: "github", brand_color: "#1f2328" }} />);
    const badge = mark(container, ".connector-badge");
    expect(badge.getAttribute("data-logo")).toBe("github");
    expect(badge.style.getPropertyValue("--brand")).toBe("#1f2328");
    expect(badge.querySelector("svg")).not.toBeNull();
  });
});
