# Frontend (web portal)

The portal is a browser-based single-page app (`apps/frontend-web/`).

- **Framework:** React 19 + React Router v7 + TypeScript 5.9
- **Build tool:** Vite 7
- **Entry point:** `src/main.tsx` — renders `<BrowserRouter><App/></BrowserRouter>`,
  initializes the Web API adapter (`initWebAPI()`), GitHub event listeners and i18n.

## Key libraries

| Concern | Library |
|---------|---------|
| UI primitives | Radix UI |
| Styling | Tailwind CSS 4 (+ typography) |
| State | Zustand |
| Validation | Zod |
| Code editor | Monaco (`@monaco-editor/react`) |
| Terminal | xterm (+ webgl addon) |
| Diagrams | Mermaid |
| Markdown | react-markdown + remark-gfm + rehype-highlight |
| Drag & drop | dnd-kit |

## Quick start (dev)

```bash
# Backend (port 3103)
cd apps/web-server && source .venv/bin/activate && python -m server.main

# Frontend (port 3100)
cd apps/frontend-web && npm install && npm run dev
```

Open <http://localhost:3100>. The token is printed on backend start and saved to
`~/.tfactory/.token`.

## Configuration (`apps/frontend-web/.env`)

| Var | Purpose |
|-----|---------|
| `VITE_API_BASE_URL` | API base (proxied to backend), default `/api` |
| `VITE_API_URL` | backend URL for the Vite proxy, e.g. `http://localhost:3103` |
| `VITE_WS_BASE_URL` | WebSocket URL for remote deployments |

For production, `npm run build` and serve the bundle from `apps/web-server/static/`.

## Conventions

- **i18n is mandatory** — all user-facing text uses `react-i18next` keys
  (`namespace:section.key`), with translations in
  `src/shared/i18n/locales/<lang>/*.json`. No hard-coded strings in JSX.
- **API responses** are wrapped client-side as `{ success, data }`; backends return
  raw objects.
- **useEffect deps:** prefer primitive ids (e.g. `selectedProjectId`) over object
  references; use refs to read state without re-running effects.
- **Persistent UI state** (skipped dialogs, etc.) uses `localStorage` with a lazy
  `useState` initializer.
