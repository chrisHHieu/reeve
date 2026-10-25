import type { Page, Request } from "@playwright/test";

// The /v1 router the routes register on. A request goes to the first route, in the order they
// were registered, whose method and path fit and whose handler answers: a handler returns the
// body to send as JSON, or nothing to let the next route have the request. One nobody answers
// gets an empty object, as a valid but empty reply.

/** A request, as a route reads it. */
export type Ask = {
  req: Request;
  path: string;
  method: string;
  query: URLSearchParams;
  /** The JSON body, or `{}` when there is none. */
  body: any;
  /** The path pattern's match: its groups are the ids in the path. */
  match: RegExpMatchArray;
};
export type Handle = (ask: Ask) => unknown | Promise<unknown>;
/** Register a route. A string path is a suffix of the request's path; `*` takes any method. */
export type On = (method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "*", path: string | RegExp, handle: Handle) => void;

export function createRouter() {
  const routes: { method: string; path: string | RegExp; handle: Handle }[] = [];
  const on: On = (method, path, handle) => void routes.push({ method, path, handle });

  const serve = (page: Page) =>
    page.route("**/v1/**", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      let body: unknown;
      const ask = (match: RegExpMatchArray): Ask => ({
        req, path: url.pathname, method: req.method(), query: url.searchParams, match,
        get body() {
          return (body ??= req.postDataJSON() || {});
        },
      });
      for (const { method, path, handle } of routes) {
        if (method !== "*" && method !== req.method()) continue;
        const match = typeof path === "string" ? (url.pathname.endsWith(path) ? [path] as RegExpMatchArray : null) : url.pathname.match(path);
        if (!match) continue;
        const answer = await handle(ask(match));
        if (answer === undefined) continue;
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(answer) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });

  return { on, serve };
}

/** A path under one session, any session: `/v1/sessions/<id>/<rest>`, `rest` a pattern's source. */
export const inSession = (rest: string) => new RegExp(`/v1/sessions/[^/]+/${rest}$`);
