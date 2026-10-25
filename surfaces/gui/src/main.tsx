import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { adoptDevToken } from "./api";
import { App } from "./App";
import { installExternalLinkRouter, isDesktop, platformOS } from "./desktop";
import { initFonts } from "./fonts";
import { initTheme } from "./theme";
import { ToastProvider } from "./ui";
// Tailwind first, so the app's own rules win where both style a thing.
import "./tailwind.css";
import "./styles.css";
import "./palettes.css";
import "./cupertino.css";
import "./ui/ui.css";
import "./ui/icon-motion.css";
import "./components/settings/settings.css";

initTheme();
initFonts();
const root = document.documentElement;
// For rules that differ by platform (html[data-platform="windows"] scrollbars, and the like).
root.dataset.platform = platformOS();
// The desktop window is dragged by CSS regions (`.desktop-shell` in styles.css).
if (isDesktop()) root.classList.add("desktop-shell");

// A file dropped anywhere but a drop target would open in the window in place of the app. The
// composer handles its own drops; this only catches the ones that miss it.
for (const event of ["dragover", "drop"]) window.addEventListener(event, (e) => e.preventDefault());

// A link out of the app opens in the system browser, since the window opens nothing itself; in a
// browser, `target="_blank"` already does that, and this does nothing.
installExternalLinkRouter();

// The token is in place before the first render, so no request goes out without it. The packaged
// app and the desktop dev shell bring their own, and there this does nothing.
adoptDevToken().finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <ToastProvider>
        <App />
      </ToastProvider>
    </StrictMode>,
  );
});
