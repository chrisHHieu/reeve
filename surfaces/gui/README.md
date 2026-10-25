# Reeve's GUI

The React app people use Reeve through. It is a client of the Python server
(`harness/interface/server/`): HTTP for reads and changes, a socket per session for the turn as it
happens, and one app-wide socket for everything else. The same code runs in a browser while
developing and inside the desktop app, an Electron shell (`electron/`).

## Before the first run

The server comes from the repository's venv. In a new checkout, make it from the repository root:

```bash
bash packaging/setup_dev_env.sh   # makes .venv, with reeve-server
```

## In a browser

Start the server, then Vite, in two terminals:

```bash
# from the repository root: the folder to work in, and the port the GUI expects
./.venv/bin/reeve-server --cwd /path/to/a/project --port 8765    # Windows: .venv\Scripts\reeve-server.exe

# from surfaces/gui
npm install      # the first time
npm run dev      # http://localhost:1420
```

A model's key can be in the environment (`OPENAI_API_KEY` and the like) or added later in
Settings ▸ Models. The GUI talks to `http://127.0.0.1:8765`; `VITE_REEVE_HTTP` and
`VITE_REEVE_WS` point it elsewhere. The server writes a new token each time it starts, in
`<state folder>/sidecar-8765.token`, and the dev server hands the current one to the page on each
load, so a restarted server needs only a reload of the page.

## As the desktop app, from source

The Electron shell wraps the same app, and starts and watches its own server on a free port, so
there is no server terminal. While developing it takes the server from the venv
(`.venv/Scripts/reeve-server.exe`, `.venv/bin/reeve-server`) and the page from Vite on :1420; a
packaged server is made only by the release scripts in `packaging/`.

```bash
cd surfaces/gui
npm install            # the first time
npm run dev            # Vite on :1420, in one terminal
npm run electron:dev   # builds the shell and opens its window, which starts the server
```

A terminal opened from inside VS Code has `ELECTRON_RUN_AS_NODE=1`, with which Electron runs as
plain Node: unset it first. `REEVE_STATE_DIR` gives the app a state folder of its own.

## Checks

```bash
npx tsc --noEmit                   # types
npm test                           # unit tests (Vitest)
npx playwright test --workers=3    # the end-to-end specs, on a mocked server: see e2e/README.md
```

