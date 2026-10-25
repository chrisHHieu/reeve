import type { Page } from "@playwright/test";

// The sockets each page's mock has open, and the helpers a spec speaks through on them as the
// server would. Every registry is a WeakMap keyed by the page, so a test starts with none.

/** One session socket, as the routes and the specs push into it. */
export type SessionChannel = { send: (type: string, data?: Record<string, unknown>) => void; close: () => void };

/** The app-wide /ws/events socket of each page. */
export const eventSockets = new WeakMap<Page, { send: (data: string) => void }>();
/** The newest session socket of each page. The routes push on it what the real server pushes
 *  from a route, such as the `compacted` marker `/compact` sends before it answers. */
export const sessionSockets = new WeakMap<Page, SessionChannel>();
/** Every open session socket of each page, by conversation, for one that is not the newest. */
export const socketsBySession = new WeakMap<Page, Map<string, SessionChannel>>();
/** Each session id a socket was opened for, once per opening: a spec reads from it whether a
 *  switch reused a socket or opened a new one, instead of guessing from timing. */
export const openedSessionSockets = new WeakMap<Page, string[]>();
/** Every frame the app sent on a session socket, oldest first: what the engine was actually told. */
export const framesSent = new WeakMap<Page, Array<Record<string, any>>>();

/** The background jobs a scripted turn started. The engine pushes this roster as a `jobs` frame
 *  whenever a job joins, prints or ends, and so does the mock, from the turn and from
 *  the kill route; `GET …/jobs` serves it for the one read a client makes as its socket opens.
 *  Empty until a spec starts a job, so no other spec's layout has the panel. */
export const fixtureJobs = new WeakMap<Page, Array<Record<string, unknown>>>();
/** The job's character count where its anchored tail ends; the streamed frames count from it. */
export const JOB_ANCHOR_TOTAL = 51_234;

/** What `registry` holds for the page, once the app has opened it: five seconds, then an error. */
async function opened<T>(page: Page, lookup: () => T | undefined, what: string): Promise<T> {
  for (let tries = 0; tries < 50 && !lookup(); tries++) await page.waitForTimeout(100);
  const found = lookup();
  if (!found) throw new Error(`the app never opened ${what}`);
  return found;
}

/** Send an event on the page's newest session socket, as the server would. */
export async function sendSessionEvent(page: Page, type: string, data: Record<string, unknown> = {}): Promise<void> {
  (await opened(page, () => sessionSockets.get(page), "a session socket")).send(type, data);
}

/** Send an event on one conversation's socket, whichever conversation is on screen. */
export async function sendSessionEventTo(page: Page, sessionId: string, type: string, data: Record<string, unknown> = {}): Promise<void> {
  const channel = await opened(page, () => socketsBySession.get(page)?.get(sessionId), `a session socket for ${sessionId}`);
  channel.send(type, data);
}

/** Send an app-wide event on /ws/events, as the server would. */
export async function sendAppEvent(page: Page, event: unknown): Promise<void> {
  (await opened(page, () => eventSockets.get(page), "/ws/events")).send(JSON.stringify(event));
}

/** Close the newest session socket from the server's end and tell nothing else, like a network
 *  drop or a sidecar restart: the app has to reconnect by itself. */
export async function dropSessionSocket(page: Page): Promise<void> {
  const channel = await opened(page, () => sessionSockets.get(page), "a session socket");
  sessionSockets.delete(page);
  channel.close();
}

/** Close one conversation's socket from the server's end, whichever is on screen. */
export async function dropSessionSocketOf(page: Page, sessionId: string): Promise<void> {
  const open = socketsBySession.get(page);
  const channel = open?.get(sessionId);
  if (!channel) throw new Error(`no open session socket for ${sessionId}`);
  open!.delete(sessionId);
  channel.close();
}

/** The frames the app has sent on session sockets, oldest first, optionally of one type. */
export function sessionFramesSent(page: Page, type?: string): Array<Record<string, any>> {
  const sent = framesSent.get(page) ?? [];
  return type ? sent.filter((frame) => frame.type === type) : sent;
}

/** How many times this page opened a socket for one conversation. */
export function sessionSocketsOpened(page: Page, sessionId: string): number {
  return (openedSessionSockets.get(page) ?? []).filter((id) => id === sessionId).length;
}
