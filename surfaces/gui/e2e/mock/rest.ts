import type { Page } from "@playwright/test";
import { createRouter } from "./router";
import { appRoutes } from "./routes/app";
import { automationRoutes, inboxRoutes } from "./routes/automations";
import { connectorRoutes } from "./routes/connectors";
import { conversationRoutes } from "./routes/conversation";
import { fileRoutes } from "./routes/files";
import { gitRoutes } from "./routes/git";
import { providerRoutes } from "./routes/providers";
import { serverRoutes } from "./routes/server";
import { planRoutes, sessionRoutes } from "./routes/session";
import type { MockState } from "./state";

// Every /v1 request the app makes, answered from the page's state. The groups are registered in
// this order and each keeps its routes' order, so a route still comes before any broader one
// that would also take its path (a session's own paths before `/v1/sessions/<id>`).

export async function routeRest(page: Page, s: MockState, door: string) {
  const { on, serve } = createRouter();
  sessionRoutes(on, s, page);
  planRoutes(on, s, page);
  gitRoutes(on, s);
  fileRoutes(on, s, door);
  conversationRoutes(on, s);
  appRoutes(on, s);
  connectorRoutes(on, s);
  providerRoutes(on, s);
  inboxRoutes(on, s);
  automationRoutes(on, s);
  serverRoutes(on, s);
  await serve(page);
}
