import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConnectorMessageCard } from "./ConnectorMessageCard";
import { itemsFromMessages } from "../itemsFromMessages";
import type { MessageSource } from "../api";

const MESSAGE: MessageSource = {
  connector: "slack",
  kind: "channel",
  channel_id: "C0BD7KZ1AH5",
  channel_name: "#reeve-test",
  sender_id: "U07JK68S4BH",
  sender_name: "Jordan Lee",
  ts: 1719700000,
  text: "Hey team — did the staging deploy go out?",
};

afterEach(cleanup);

const header = (container: HTMLElement) => container.querySelector(".connector-card-head") as HTMLElement;

describe("ConnectorMessageCard", () => {
  it("shows who wrote where, through which connector, and what they wrote, with the ids kept back", () => {
    render(<ConnectorMessageCard source={MESSAGE} />);
    for (const text of ["#reeve-test", "Jordan Lee", "via Slack"]) expect(screen.getByText(text)).toBeTruthy();
    expect(screen.getByText(/staging deploy go out/)).toBeTruthy();
    expect(screen.queryByText(/C0BD7KZ1AH5/)).toBeNull();
  });

  it("wears the connector's badge and is marked with its id", () => {
    const { container } = render(<ConnectorMessageCard source={MESSAGE} />);
    expect(container.querySelector(".connector-badge svg")).not.toBeNull();
    expect(container.querySelector(".connector-card[data-brand='slack']")).not.toBeNull();
  });

  it("a connector the registry does not know gets the plug and the neutral colour", () => {
    const { container } = render(<ConnectorMessageCard source={{ ...MESSAGE, connector: "does-not-exist" }} />);
    expect(container.querySelector(".connector-card[data-brand='fallback']")).not.toBeNull();
    expect(container.querySelector(".connector-badge svg")).not.toBeNull();
  });

  it("hovering the header shows the ids in place of the names, and leaving brings the names back", () => {
    const { container } = render(<ConnectorMessageCard source={MESSAGE} />);
    fireEvent.mouseEnter(header(container));
    expect(screen.getByText(/C0BD7KZ1AH5/)).toBeTruthy();
    expect(screen.getByText(/U07JK68S4BH/)).toBeTruthy();
    expect(screen.queryByText("Jordan Lee")).toBeNull();
    expect(screen.queryByText("#reeve-test")).toBeNull();
    fireEvent.mouseLeave(header(container));
    expect(screen.getByText("Jordan Lee")).toBeTruthy();
    expect(screen.queryByText(/C0BD7KZ1AH5/)).toBeNull();
  });

  it("focusing the header from the keyboard shows the ids too", () => {
    const { container } = render(<ConnectorMessageCard source={MESSAGE} />);
    fireEvent.focus(header(container));
    expect(screen.getByText(/C0BD7KZ1AH5/)).toBeTruthy();
  });
});

describe("itemsFromMessages", () => {
  it("a person's message that carries a connector source becomes a `connector` item", () => {
    const [item, ...rest] = itemsFromMessages([{ role: "user", content: "💬 New message on slack:#reeve-test …", source: MESSAGE }]);
    expect(rest).toHaveLength(0);
    expect(item.kind).toBe("connector");
    expect((item as Extract<typeof item, { kind: "connector" }>).source.channel_name).toBe("#reeve-test");
  });

  it("one without a source stays the person's own bubble", () => {
    const items = itemsFromMessages([{ role: "user", content: "hello there" }]);
    expect(items.map((item) => item.kind)).toEqual(["user"]);
  });
});
