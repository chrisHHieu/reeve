// What the app's page gets from the shell: the server's address and this launch's token,
// set before the app's own code runs, and one bridge, `reeveDesktop`, to ask the shell for things
// and hear from it. Nothing else of Electron or Node reaches the page.
import { contextBridge, ipcRenderer } from "electron";

const config = ipcRenderer.sendSync("reeve:config") as { http: string; ws: string; token: string; platform: string } | null;
if (config) {
  contextBridge.exposeInMainWorld("__REEVE_HTTP__", config.http);
  contextBridge.exposeInMainWorld("__REEVE_WS__", config.ws);
  contextBridge.exposeInMainWorld("__REEVE_API_TOKEN__", config.token);
  contextBridge.exposeInMainWorld("__REEVE_PLATFORM__", config.platform);
}

contextBridge.exposeInMainWorld("reeveDesktop", {
  invoke: (cmd: string, args?: Record<string, unknown>) => ipcRenderer.invoke("reeve:invoke", cmd, args),
  on: (name: string, listener: (payload: unknown) => void) => {
    const hear = (_event: unknown, channel: string, payload: unknown) => {
      if (channel === name) listener(payload);
    };
    ipcRenderer.on("reeve:event", hear);
    return () => ipcRenderer.removeListener("reeve:event", hear);
  },
});
