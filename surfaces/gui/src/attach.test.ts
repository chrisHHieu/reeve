import { describe, expect, it } from "vitest";

import { mergeCapped, MAX_ATTACHMENTS, pastedFiles, readFile, readFileChecked } from "./attach";

const file = (name: string, type: string, body: BlobPart) =>
  new File([body], name, { type });

describe("readFile", () => {
  it("classifies a text file as text", async () => {
    const a = await readFile(file("notes.md", "text/markdown", "# hi"));

    expect(a?.kind).toBe("text");
    expect(a?.text).toContain("# hi");
  });

  it("keeps any other file as a data-URL file part, not a drop", async () => {
    const a = await readFile(
      file("book.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "PK\u0003\u0004"),
    );

    expect(a?.kind).toBe("file");
    expect(a?.name).toBe("book.docx");
    expect(a?.data_url?.startsWith("data:")).toBe(true);
  });

  it("still refuses a file over the size cap", async () => {
    const big = new File([new Uint8Array(11 * 1024 * 1024)], "big.bin", {
      type: "application/octet-stream",
    });

    expect(await readFile(big)).toBeNull();
  });
});

describe("readFileChecked", () => {
  it("says why a file was not attached", async () => {
    const big = new File([new Uint8Array(11 * 1024 * 1024)], "big.png", { type: "image/png" });
    expect(await readFileChecked(big)).toEqual({
      ok: false,
      reason: "big.png skipped — 11.0 MB is over the 10 MB limit",
    });
    const empty = await readFileChecked(new File([], "folder"));
    expect(empty.ok).toBe(false);
  });

  it("keeps UTF-8 text as text", async () => {
    const outcome = await readFileChecked(file("vi.csv", "text/csv", "Báo cáo,1"));
    expect(outcome.ok && outcome.attachment).toMatchObject({ kind: "text", text: "Báo cáo,1" });
  });

  it("does not guess an encoding: text that is not UTF-8 goes over as bytes", async () => {
    // "cáo" as cp1258 / cp1252: 0xE1 alone is not valid UTF-8, and the browser default would
    // have turned it into a replacement character
    const outcome = await readFileChecked(file("cp.csv", "text/csv", new Uint8Array([0x63, 0xe1, 0x6f, 0x0a])));
    expect(outcome.ok && outcome.attachment.kind).toBe("file");
    expect(outcome.ok && outcome.attachment.data_url).toContain("data:");
    expect(outcome.ok && outcome.attachment.text).toBeUndefined();
  });
});

describe("mergeCapped", () => {
  const att = (n: number) => ({ kind: "text" as const, name: `n${n}.txt`, text: `x${n}` });

  it("counts what did not fit instead of losing it", () => {
    const cur = Array.from({ length: 6 }, (_, i) => att(i));
    const { merged, dropped } = mergeCapped(cur, [att(6), att(7), att(8), att(9)]);
    expect(merged).toHaveLength(MAX_ATTACHMENTS);
    expect(dropped).toBe(2);
  });

  it("bounds the total size, because one frame over the socket limit closes the connection", () => {
    const big = (n: number) => ({ kind: "file" as const, name: `v${n}.mp4`, data_url: "data:video/mp4;base64," + "A".repeat(9_000_000) });
    const { merged, dropped } = mergeCapped([big(1)], [big(2)]);
    expect(merged).toHaveLength(1);
    expect(dropped).toBe(1);
    expect(mergeCapped([], [big(1)]).dropped).toBe(0);
  });

  it("collapses a duplicate without counting it as dropped", () => {
    const { merged, dropped } = mergeCapped([att(1)], [att(1), att(2)]);
    expect(merged.map((a) => a.name)).toEqual(["n1.txt", "n2.txt"]);
    expect(dropped).toBe(0);
  });
});

describe("pastedFiles", () => {
  const item = (kind: string, type: string, f?: File) => ({ kind, type, getAsFile: () => f ?? null });
  const png = new File(["p"], "s.png", { type: "image/png" });
  const pdf = new File(["p"], "a.pdf", { type: "application/pdf" });

  it("takes a screenshot", () => {
    expect(pastedFiles([item("file", "image/png", png)])).toEqual([png]);
  });

  it("takes a file copied in Explorer, which has no text beside it", () => {
    expect(pastedFiles([item("file", "application/pdf", pdf)])).toEqual([pdf]);
  });

  it("leaves a spreadsheet copy alone: the text is what was meant, not its picture-less files", () => {
    // Cells copied from Excel carry text/plain AND a rendering; only the image would be attached
    expect(pastedFiles([item("string", "text/plain"), item("file", "application/pdf", pdf)])).toEqual([]);
    expect(pastedFiles([item("string", "text/plain"), item("file", "image/png", png)])).toEqual([png]);
  });
});
