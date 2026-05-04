# Client (Vite + React)

## Local dev (with this repo’s API)

The Vite dev server **proxies** `/settings`, `/system`, `/dealers`, `/fill-forms`, etc. to **`http://127.0.0.1:8000`**. **`vite.config.ts`** disables Node’s default **5‑minute** `requestTimeout` on the dev HTTP server (otherwise the browser connection can drop while uvicorn is still busy), raises **`proxyTimeout`**, and clears proxy socket timeouts for **`/fill-forms`**, **`/uploads`**, and **`/subdealer-challan`**. **Restart `npm run dev`** after changing it. Leave **`VITE_API_URL`** unset locally so requests use this proxy (direct `:8000` calls skip the proxy but still need long client timeouts in **`fillForms.ts`**). If the backend is not running, the terminal shows `http proxy error` / **`ECONNREFUSED 127.0.0.1:8000`**.

1. In one terminal, from the **`backend`** folder (with venv activated):  
   `python -m uvicorn app.main:app --reload --port 8000`
2. In another, from **`client`**:  
   `npm run dev`

Or use **`daily_startup.bat`** at the project root to start both.

**Browser-only dev + DMS / Playwright:** automation runs inside the uvicorn process on a **single** worker thread. If you close the Edge/DMS window mid-run, **Retry** can stay queued until that thread unwinds. **Release Browsers** clears ports and cache; if the API still reports a stuck worker (`playwright_disconnect_ok: false`) or Retry never returns, **restart uvicorn** (Ctrl+C, start again), refresh the SPA, then Retry. Use the packaged **Electron** app when you want the stronger sidecar “release” path.

---

# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
