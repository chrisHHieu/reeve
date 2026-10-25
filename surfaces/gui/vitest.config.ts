import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The unit tests' config, apart from vite.config.ts so a build is never touched by it. Tests sit
// beside what they test: `*.test.ts(x)` under src/, and the shell's under electron/.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}", "electron/**/*.test.ts"],
    // A few tests read stylesheets as text (`?raw`): the palettes', the type scale's, the fonts'.
    // Vitest makes every CSS import an empty string unless the file is listed here.
    css: { include: [/styles\.css/, /palettes\.css/, /fonts\.css/] },
  },
});
