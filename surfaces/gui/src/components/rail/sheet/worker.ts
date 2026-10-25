// The workbook, held off the page's thread. The view asks for the sheet it shows and the cells
// in sight; reading a large file happens here, so the app keeps moving while it does.
import { readBook, type Book, type Rect } from "./book";

type Ask = { id: number; op: "open"; data: string } | { id: number; op: "sheet"; index: number } | { id: number; op: "cells"; index: number; rects: Rect[] };

const scope = self as unknown as { onmessage: ((event: MessageEvent<Ask>) => void) | null; postMessage(message: unknown): void };
let book: Promise<Book> | null = null;

scope.onmessage = (event) => {
  const ask = event.data;
  const answer = async () => {
    if (ask.op === "open") {
      book = readBook(ask.data);
      return (await book).info;
    }
    if (!book) throw new Error("no workbook is open");
    const open = await book;
    return ask.op === "sheet" ? open.sheet(ask.index) : open.cells(ask.index, ask.rects);
  };
  answer().then(
    (value) => scope.postMessage({ id: ask.id, ok: true, value }),
    (error: unknown) => scope.postMessage({ id: ask.id, ok: false, error: error instanceof Error ? error.message : String(error) }),
  );
};
