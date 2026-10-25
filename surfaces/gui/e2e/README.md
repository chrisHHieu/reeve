# GUI end-to-end specs

The specs here run the real GUI in Chromium against a mock of the server. Every `/v1` request and
every socket the app opens is answered inside the test, so no Python process runs, nothing on the
machine is touched, and no model is called. A spec checks what the person sees, through the app's
own reads of what it changed.

## Running them

```bash
npm run e2e                                    # every spec, headless
npx playwright test --workers=3                # on a 16-core machine: more workers starve it
npx playwright test e2e/settings.spec.ts       # one file
npm run e2e:ui                                 # Playwright's UI, to watch and step through
```

`playwright.config.ts` starts the Vite dev server on port 5199, apart from `npm run dev` on 5173,
and uses one already running there. Under load a few specs time out now and then; run a failure
again on its own before calling it a regression.

`npm run e2e:live` runs `e2e-live/` instead, against the real server on :8765, one worker
(`playwright.live.config.ts`). Each spec skips when that server is not up. `api-smoke` reads the
shapes the GUI depends on and spends nothing; the others start real sessions on the configured
model, so they cost tokens and their answers vary. They are run by hand, never in CI.

## The mock

`fixtures.ts` is what a spec imports. Its `test` puts the mock on the page before it navigates,
and it exports `expect`, `seedSessionMessages`, `openConnectors` and the socket helpers
(`sendSessionEvent`, `sendSessionEventTo`, `sendAppEvent`, `dropSessionSocket`,
`dropSessionSocketOf`, `sessionFramesSent`, `sessionSocketsOpened`). The mock itself is in `mock/`:

| File | What it holds |
| --- | --- |
| `data.ts` | The fixed data, in the shapes the real server answers with |
| `state.ts` | One page's own copy of it, which the routes change |
| `router.ts` | The `/v1` router: the first route whose method and path fit, and that answers, wins |
| `routes/*.ts` | The routes, by area, in the order they are tried |
| `turns.ts` | The fake agent's turns, matched on the message's words |
| `sockets.ts` | The app-wide event stream, each session's socket, the terminal |
| `channels.ts` | The sockets each page opened, and the helpers that send on them |
| `door.ts` | The preview door and the pages it serves |

A route returns the body it answers with, or nothing to let the next one take the request; a
request no route takes gets `{}`. A string path matches as the end of the request's path. The order
matters where one path is a prefix of another: a session's own paths are registered before
`/v1/sessions/<id>`.

The session socket speaks the real `{type, data}` protocol. It sends `ready` when it opens, and
answers a message with `turn_start`, then the turn `turns.ts` has for its words, or an echo:
`Echo: <text> [model=…]`, with the same usage every time (10k tokens). A turn that waits on the
person (an approval, a question, a plan, a tool to install) sends no `turn_done` until the answer
frame comes back. The words that start a turn are the patterns at the head of each entry in
`turns.ts`; "run a tool", "stream the epic", "edit the upload client" and "start a background job"
are the ones most specs use.

What the data starts with, worth knowing when reading a spec:

- **Sessions**: "Draft the launch note" is pinned and the newest, so the app opens on it. Seven
  "Weekly plan N" follow, past the sidebar's peek of five; the third waits on an approval. "Ops
  triage" was recorded under a persona that is gone, and one session came from a Slack mention.
- **Providers**: OpenAI connected and used, Anthropic connected and never used, the
  OpenAI-compatible vendors not set up, Ollama with no key needed, and the ChatGPT subscription
  signed out. The key check fails any key with "bad" in it.
- **Connectors**: Slack and GitHub connected; Gmail, Calendar and Notion with no account until a
  sign-in, which finishes at once; monday and Jira on their vendors' MCP servers.
- **Automations**: "Daily AI News" with a run going, and "Weekly CRM digest", quiet.

A saved transcript is empty for every session. `seedSessionMessages(page, id, messages)` adds a
later route, which wins, serving `messages` for that session: calls and their results, the
`_presentation` and `_display` a tool saved, reasoning, notices, messages from a connector. That is
how a spec tests a reopened session (`itemsFromMessages`), which the live echo cannot reach.

## Writing a spec

```ts
import { test, expect } from "./fixtures";
import { openPinned, say } from "./steps";

test("what the person sees, said as a sentence", async ({ page }) => {
  await openPinned(page);
  await say(page, "hello");
  await expect(page.getByText("Echo: hello", { exact: false })).toBeVisible();
});
```

`steps.ts` has the steps many specs take: opening the pinned session or Settings at a page,
sending a message, and reading the body of the request an action sends. A flow that reads an
endpoint the mock does not have needs a route in `mock/routes/`: the `{}` it gets otherwise breaks
a component that expects a list. A test that needs an answer of its own adds a `page.route`
before navigating, and that route wins over the mock's.

Prefer `getByRole` and test ids. A control whose accessible name comes from its contents (an ✕, a
chip) is easier to reach with `getByTitle` or `getByLabel`. When a measure depends on layout,
poll it (`expect.poll`) rather than reading it once: a panel can still be taking its width.
