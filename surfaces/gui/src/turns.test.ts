// An image the AGENT looked at is not the user speaking.
//
// `read_image` hands the model a picture, and the only portable way to do that is a
// user-role message right after the tool result — no provider accepts an image inside one.
// The backend tags those messages and routes eight landmarks through `is_user_turn`
// (harness/turns.py). Two GUI landmarks were never routed, and both broke the way that
// module's docstring said they would:
//
//   · the transcript rendered the agent's own screenshot as a bubble from the user, so the
//     surface claimed the person had sent something they never sent ("tôi có gửi đâu",
//     owner-hit 2026-08-26);
//   · the replayed plan strip cleared mid-turn, because an attachment read as the start of
//     a new turn — the exact staleness item 1.13 exists to prevent, arriving from the other
//     direction.
//
// These are one rule, so they are tested together.
import { describe, expect, it } from "vitest";
import { itemsFromMessages } from "./itemsFromMessages";
import { planFromMessages } from "./todoPlan";
import { ATTACHED_BY, attachedBy, isUserTurn } from "./turns";
import type { ConversationMessage } from "./api";

const PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

const readImageCall = (id: string, path: string): ConversationMessage => ({
  role: "assistant",
  content: "",
  tool_calls: [
    { id, function: { name: "read_image", arguments: JSON.stringify({ path }) } },
  ],
});

const toolResult = (id: string): ConversationMessage => ({
  role: "tool",
  tool_call_id: id,
  content: JSON.stringify({ shown: "slide1.png" }),
});

const attachment = (id: string): ConversationMessage => ({
  role: "user",
  content: [{ type: "image_url", image_url: { url: PNG } }],
  [ATTACHED_BY]: id,
});

const said = (text: string): ConversationMessage => ({ role: "user", content: text });

// -- the predicate ------------------------------------------------------------------

describe("isUserTurn", () => {
  it("is true for a person's message", () => {
    expect(isUserTurn(said("check my slides"))).toBe(true);
  });

  it("is false for content the engine attached", () => {
    expect(isUserTurn(attachment("c1"))).toBe(false);
  });

  it("stays true for a connector-delivered turn, which IS a person", () => {
    // Background delivery carries a `source` sidecar and is still someone talking.
    const delivered = { role: "user", content: "hi", source: { connector: "slack" } } as ConversationMessage;
    expect(isUserTurn(delivered)).toBe(true);
  });

  it("survives junk instead of a message", () => {
    expect(isUserTurn(null)).toBe(false);
    expect(isUserTurn(undefined)).toBe(false);
  });

  it("ignores an empty tag rather than treating it as an owner", () => {
    expect(attachedBy({ role: "user", content: "hi", [ATTACHED_BY]: "" })).toBeNull();
    expect(isUserTurn({ role: "user", content: "hi", [ATTACHED_BY]: "" })).toBe(true);
  });
});

// -- the transcript -----------------------------------------------------------------

describe("the transcript", () => {
  const transcript = [
    said("Find an insight in this data"),
    readImageCall("c1", "/export/slide1.png"),
    toolResult("c1"),
    attachment("c1"),
  ];

  it("does not show the agent's image as something the user sent", () => {
    const users = itemsFromMessages(transcript).filter((i) => i.kind === "user");
    expect(users).toHaveLength(1);
    expect(users[0]).toMatchObject({ text: "Find an insight in this data" });
    expect(users[0].attachments ?? []).toEqual([]);
  });

  it("puts the image on the step that asked for it", () => {
    const tool = itemsFromMessages(transcript).find((i) => i.kind === "tool");
    expect(tool?.name).toBe("read_image");
    expect(tool?.attachments).toHaveLength(1);
    expect(tool?.attachments?.[0].data_url).toBe(PNG);
  });

  it("still shows an image the user really did send", () => {
    // The whole point is telling the two apart — losing the person's own attachment
    // would be the same defect wearing the opposite sign.
    const items = itemsFromMessages([
      {
        role: "user",
        content: [
          { type: "text", text: "what is wrong with this?" },
          { type: "image_url", image_url: { url: PNG } },
        ],
      },
    ]);
    const user = items.find((i) => i.kind === "user");
    expect(user?.attachments).toHaveLength(1);
    expect(user?.text).toBe("what is wrong with this?");
  });

  it("keeps each image with its own step when several are read", () => {
    const items = itemsFromMessages([
      said("review all the slides"),
      readImageCall("c1", "/slide1.png"),
      toolResult("c1"),
      attachment("c1"),
      readImageCall("c2", "/slide2.png"),
      toolResult("c2"),
      attachment("c2"),
    ]);
    const tools = items.filter((i) => i.kind === "tool");
    expect(tools).toHaveLength(2);
    expect(tools[0].attachments).toHaveLength(1);
    expect(tools[1].attachments).toHaveLength(1);
    expect(items.filter((i) => i.kind === "user")).toHaveLength(1);
  });

  it("drops an attachment whose call is not in the transcript, rather than inventing a bubble", () => {
    // Compaction can drop the assistant message while the attachment survives. Nothing to
    // hang it on is a reason to show nothing — never a reason to blame the user for it.
    const items = itemsFromMessages([said("hello"), attachment("gone")]);
    expect(items.filter((i) => i.kind === "user")).toHaveLength(1);
    expect(items.every((i) => i.kind !== "tool")).toBe(true);
  });
});

// -- the plan strip -----------------------------------------------------------------

describe("the replayed plan", () => {
  const plan = [
    { content: "Read the deck", status: "in_progress" },
    { content: "Write the note", status: "pending" },
  ];
  const writePlan: ConversationMessage = {
    role: "assistant",
    content: "",
    tool_calls: [
      {
        id: "t0",
        function: { name: "todo_write", arguments: JSON.stringify({ todos: plan }) },
      },
    ],
  };

  it("survives the agent looking at an image mid-turn", () => {
    const got = planFromMessages([
      said("review the deck"),
      writePlan,
      readImageCall("c1", "/slide1.png"),
      toolResult("c1"),
      attachment("c1"),
    ]);
    expect(got).toHaveLength(2);
    expect(got[0].content).toBe("Read the deck");
  });

  it("the person taking a new turn does not clear it — only a new write does", () => {
    // Owner call 2026-09-04: a turn that dies (or a stray assistant line) must not wipe
    // the standing plan; supersession is the model's next write, nothing else.
    const got = planFromMessages([said("review the deck"), writePlan, said("actually, stop")]);
    expect(got).toHaveLength(2);
    expect(got[0].content).toBe("Read the deck");
  });
});
