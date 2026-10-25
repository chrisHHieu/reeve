// What a message carried, drawn above its bubble: the facts a card says, the pictures and
// files of a sent message, how a reopened conversation gets them back, and how the message just sent
// learns where its files were kept.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Attachment, Item, WsEvent } from "../../types";
import { itemsFromMessages } from "../../itemsFromMessages";
import { LiveSession } from "../../live/liveSession";
import { handleSessionEvent, type SessionEventHost } from "../../live/sessionEvents";
import { OPEN_ARTIFACT_EVENT } from "../Markdown";
import { Transcript } from "../Transcript";
import { attachedTotal, attachmentMeta } from "./attachmentFacts";
import { MessageAttachments } from "./MessageAttachments";

afterEach(cleanup);

const PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const picture = (name: string): Attachment => ({ kind: "image", name, data_url: PIXEL });
const file = (name: string, extra: Partial<Attachment> = {}): Attachment => ({ kind: "pdf", name, size: 2_516_582, ...extra });

describe("what a card says", () => {
  it("names the kind in a word with its size, or its length when it is text", () => {
    expect(attachmentMeta(file("Q3-report.pdf"))).toBe("PDF · 2.4 MB");
    expect(attachmentMeta({ kind: "text", name: "sales.csv", text: "month,total\njan,3\nfeb,4\n" })).toBe("CSV · 2 rows");
    expect(attachmentMeta({ kind: "text", name: "Composer.tsx", lines: 2930 })).toBe("TypeScript · 2,930 lines");
    expect(attachmentMeta({ kind: "file", name: "blob", size: 900 })).toBe("File · 900 B");
    expect(attachedTotal([file("a.pdf"), file("b.pdf", { size: 1024 * 300 })])).toBe("2 attached · 2.7 MB");
  });
});

describe("a sent message's attachments", () => {
  it("cuts six pictures to four tiles, the last saying how many more, and opens them large", () => {
    render(<MessageAttachments attachments={[1, 2, 3, 4, 5, 6].map((n) => picture(`shot-${n}.png`))} />);
    const tiles = screen.getAllByTestId("attachment-tile");
    expect(tiles).toHaveLength(4);
    expect(tiles[3].textContent).toBe("+3");
    fireEvent.click(tiles[1].querySelector("button")!);
    expect(screen.getByTestId("md-image-viewer")).toBeTruthy();
    expect(screen.getByTestId("md-image-count").textContent).toBe("2 / 6");
  });

  it("shows three files of seven and the rest on a click; a kept file opens in Files", () => {
    const opened: string[] = [];
    const listen = (e: Event) => opened.push(String((e as CustomEvent).detail?.path));
    window.addEventListener(OPEN_ARTIFACT_EVENT, listen);
    const files = [1, 2, 3, 4, 5, 6, 7].map((n) => file(`invoice-${n}.pdf`, n === 1 ? { path: "/s/attachments/invoice-1.pdf" } : {}));
    render(<MessageAttachments attachments={files} />);
    expect(screen.getAllByTestId("attachment-card")).toHaveLength(3);
    fireEvent.click(screen.getByLabelText("Open invoice-1.pdf"));
    expect(opened).toEqual(["/s/attachments/invoice-1.pdf"]);
    // A file with no kept copy says what it was, and is not a button that does nothing.
    expect(screen.queryByLabelText("Open invoice-2.pdf")).toBeNull();
    fireEvent.click(screen.getByText("4 more files"));
    expect(screen.getAllByTestId("attachment-card")).toHaveLength(7);
    window.removeEventListener(OPEN_ARTIFACT_EVENT, listen);
  });

  it("draws a message that is only a picture with no bubble under it", () => {
    const items: Item[] = [{ kind: "user", text: "", attachments: [picture("error-dialog.png")] }];
    const { container } = render(<Transcript items={items} running={false} onApprove={vi.fn()} />);
    expect(screen.getAllByTestId("attachment-tile")).toHaveLength(1);
    expect(container.querySelector(".bubble-user")).toBeNull();
  });
});

describe("a picture a tool returned", () => {
  it("opens large from its row, like every other picture in the conversation", () => {
    const items: Item[] = [
      { kind: "user", text: "check the render" },
      { kind: "tool", id: "t1", callId: "c1", name: "read_image", args: { path: "f_0_00.png" }, status: "ok", attachments: [picture("f_0_00.png")] },
    ];
    render(<Transcript items={items} running={false} onApprove={vi.fn()} />);
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    fireEvent.click(screen.getByTestId("tool-image"));
    expect(screen.getByTestId("md-image-viewer")).toBeTruthy();
  });
});

describe("a reopened conversation", () => {
  const rows = [
    { kind: "image", name: "shot.png", size: 70 },
    { kind: "text", name: "notes.md", size: 12, lines: 2, path: "/s/attachments/notes.md" },
    { kind: "pdf", name: "Q3-report.pdf", size: 2_516_582, path: "/s/attachments/Q3-report.pdf" },
  ];
  const content = [
    { type: "text", text: "compare these" },
    { type: "image_url", image_url: { url: PIXEL } },
    { type: "text", text: "[Attached file: notes.md]\n# hi\nthere" },
    { type: "file", file: { filename: "Q3-report.pdf", file_data: "data:application/pdf;base64,JVBERg==" } },
  ];

  it("draws each attachment from its row, and the bubble keeps only the person's words", () => {
    const [user] = itemsFromMessages([{ role: "user", content, _attached: rows }]) as Array<Extract<Item, { kind: "user" }>>;
    expect(user.text).toBe("compare these");
    expect(user.attachments?.map((a) => [a.kind, a.name, a.path ?? null])).toEqual([
      ["image", "shot.png", null],
      ["text", "notes.md", "/s/attachments/notes.md"],
      ["pdf", "Q3-report.pdf", "/s/attachments/Q3-report.pdf"],
    ]);
    expect(user.attachments?.[0].data_url).toBe(PIXEL);
  });

  it("reads an older message off its content: a text file is a card, not a block of the bubble", () => {
    const [user] = itemsFromMessages([{ role: "user", content }]) as Array<Extract<Item, { kind: "user" }>>;
    expect(user.text).toBe("compare these");
    expect(user.attachments?.map((a) => [a.kind, a.name])).toEqual([
      ["image", "image"],
      ["text", "notes.md"],
      ["pdf", "Q3-report.pdf"],
    ]);
  });
});

describe("the message just sent", () => {
  it("learns where its files were kept from the turn's start, so its cards open now", () => {
    const live = new LiveSession("s");
    const host: SessionEventHost = {
      factsChanged: () => {},
      railStale: () => {},
      reloadTranscript: () => {},
      engineReplaced: () => {},
      turnEnded: () => {},
      showsEnvironmentError: () => false,
    };
    live.setItems([{ kind: "user", text: "read this", attachments: [{ kind: "pdf", name: "a.pdf", data_url: "data:application/pdf;base64,JVBERg==" }] }]);
    const frame = { type: "turn_start", data: { input: [], attached: [{ kind: "pdf", name: "a.pdf", size: 4, path: "/s/attachments/a.pdf" }] } };
    handleSessionEvent(live, frame as unknown as WsEvent, host);
    const user = live.current.items[0] as Extract<Item, { kind: "user" }>;
    expect(user.attachments?.[0].path).toBe("/s/attachments/a.pdf");
  });
});
