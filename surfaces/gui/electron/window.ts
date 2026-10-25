// The window, the tray, and closing and quitting (kept through the move to Electron).
// Closing the window does what the person chose once: "tray" hides it and Reeve keeps
// running (work in progress and automations go on), "quit" quits, asking first when that would stop
// work. Until the choice is made the app asks. An app that does not answer within 1.5 s (still
// loading, or gone) does not leave the close button dead: the shell does what was chosen, and hides
// when nothing was. The tray says what runs, and quitting asks when it would stop work.
import { app, BrowserWindow, Menu, nativeImage, Tray } from "electron";
import path from "node:path";
import { closeBehavior, trayLine } from "./policy";
import { readPrefs } from "./paths";

const ACK_WAIT_MS = 1500;

export class Shell {
  window: BrowserWindow | null = null;
  private tray: Tray | null = null;
  private running = 0;
  private needsYou = 0;
  private titles = "";
  private quitWhenDone = false;
  private closeAcked = false;
  /** Set once quitting is decided, so the window's close no longer stops it. */
  quitting = false;

  constructor(private readonly iconDir: string) {}

  show(): void {
    const win = this.window;
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  }

  send(channel: string, payload?: unknown): void {
    this.window?.webContents.send("reeve:event", channel, payload);
  }

  /** Wire a window's close to the person's choice. */
  watchClose(win: BrowserWindow): void {
    win.on("close", (event) => {
      if (this.quitting) return;
      event.preventDefault();
      const behavior = closeBehavior(readPrefs().on_close);
      if (behavior === "tray") {
        win.hide();
        return;
      }
      this.closeAcked = false;
      this.send("reeve:close-requested", { behavior });
      setTimeout(() => {
        if (this.closeAcked) return;
        if (behavior === "quit") this.requestQuit();
        else win.hide();
      }, ACK_WAIT_MS);
    });
  }

  ackClose(): void {
    this.closeAcked = true;
  }

  hideToTray(): void {
    this.window?.hide();
  }

  quit(): void {
    this.quitting = true;
    app.quit();
  }

  /** Quit, unless it would stop work: then the window comes forward and the app asks. */
  requestQuit(): void {
    if (this.running === 0) {
      this.quit();
      return;
    }
    this.show();
    this.send("reeve:quit-requested");
  }

  /** What the app says is running across its conversations. */
  setStatus(running: number, needsYou: number, titles: string): void {
    this.running = running;
    this.needsYou = needsYou;
    this.titles = titles;
    this.drawTray();
    if (this.quitWhenDone && running === 0) this.quit();
  }

  setQuitWhenDone(on: boolean): boolean {
    this.quitWhenDone = on;
    this.drawTray();
    if (on && this.running === 0) this.quit();
    return on;
  }

  createTray(): void {
    const icon =
      process.platform === "darwin"
        ? nativeImage.createFromPath(path.join(this.iconDir, "trayTemplate.png"))
        : nativeImage.createFromPath(path.join(this.iconDir, "32x32.png"));
    if (process.platform === "darwin") icon.setTemplateImage(true);
    this.tray = new Tray(icon);
    this.tray.on("click", () => this.show());
    this.drawTray();
  }

  private drawTray(): void {
    if (!this.tray) return;
    const line = trayLine(this.running, this.needsYou, this.titles);
    this.tray.setToolTip(line === "Nothing running" ? "Reeve" : `Reeve · ${line}`);
    this.tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: line, enabled: false },
        { type: "separator" },
        { label: "Open Reeve", click: () => this.show() },
        {
          label: "Settings",
          click: () => {
            this.show();
            void this.window?.webContents.executeJavaScript("window.dispatchEvent(new CustomEvent('reeve:open-settings'))");
          },
        },
        { type: "separator" },
        {
          label: "Quit when they finish",
          type: "checkbox",
          checked: this.quitWhenDone,
          enabled: this.running > 0,
          click: () => this.setQuitWhenDone(!this.quitWhenDone),
        },
        { label: "Quit Reeve", click: () => this.requestQuit() },
      ]),
    );
  }
}
