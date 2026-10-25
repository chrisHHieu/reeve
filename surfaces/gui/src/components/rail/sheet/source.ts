// Where the sheet view gets a workbook from: a worker when the window has one, this thread
// otherwise (the test DOM, or a webview that will not start the worker). Both answer the same
// three questions, and every answer is asked for, never read in place, so which one it is does
// not change the view.
import { readBook, type Book, type BookInfo, type CellView, type Rect, type SheetInfo } from "./book";

export interface SheetSource {
  open(): Promise<BookInfo>;
  sheet(index: number): Promise<SheetInfo>;
  cells(index: number, rects: Rect[]): Promise<CellView[]>;
  close(): void;
}

/** The workbook read on this thread. */
export function localSource(base64: string): SheetSource {
  let book: Promise<Book> | null = null;
  const opened = () => (book ??= readBook(base64));
  return {
    open: async () => (await opened()).info,
    sheet: async (index) => (await opened()).sheet(index),
    cells: async (index, rects) => (await opened()).cells(index, rects),
    close: () => {
      book = null;
    },
  };
}

class WorkerDown extends Error {}

type Reply = { id: number; ok: true; value: unknown } | { id: number; ok: false; error: string };

export function openSource(base64: string): SheetSource {
  let worker: Worker | null = null;
  try {
    if (typeof Worker !== "undefined") worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
  } catch {
    worker = null;
  }
  if (!worker) return localSource(base64);

  const waiting = new Map<number, { resolve(value: unknown): void; reject(error: Error): void }>();
  let next = 1;
  let down = false;
  let local: SheetSource | null = null;
  const fail = () => {
    down = true;
    waiting.forEach((ask) => ask.reject(new WorkerDown("the reader stopped")));
    waiting.clear();
  };
  worker.onmessage = (event: MessageEvent<Reply>) => {
    const ask = waiting.get(event.data.id);
    if (!ask) return;
    waiting.delete(event.data.id);
    if (event.data.ok) ask.resolve(event.data.value);
    else ask.reject(new Error(event.data.error));
  };
  worker.onerror = fail;
  worker.onmessageerror = fail;

  const ask = <T>(message: Record<string, unknown>): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      if (down || !worker) return reject(new WorkerDown("the reader stopped"));
      const id = next++;
      waiting.set(id, { resolve: resolve as (value: unknown) => void, reject });
      worker.postMessage({ id, ...message });
    });
  // A worker that never started is not a file that cannot be read: read it here instead.
  const through = <T>(viaWorker: () => Promise<T>, viaLocal: (source: SheetSource) => Promise<T>): Promise<T> =>
    local
      ? viaLocal(local)
      : viaWorker().catch((error: unknown) => {
          if (!(error instanceof WorkerDown)) throw error;
          local ??= localSource(base64);
          return local.open().then(() => viaLocal(local as SheetSource));
        });

  return {
    open: () => through(() => ask<BookInfo>({ op: "open", data: base64 }), (source) => source.open()),
    sheet: (index) => through(() => ask<SheetInfo>({ op: "sheet", index }), (source) => source.sheet(index)),
    cells: (index, rects) => through(() => ask<CellView[]>({ op: "cells", index, rects }), (source) => source.cells(index, rects)),
    close: () => {
      down = true;
      waiting.clear();
      worker?.terminate();
      worker = null;
      local?.close();
    },
  };
}
