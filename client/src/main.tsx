import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { clearAccessToken } from "./auth/token";
import "./index.css";
import App from "./App.tsx";

/**
 * Full page load (including F5 / address-bar refresh): drop any persisted session token so the
 * shell always starts at the login screen. A session exists only until the next full reload.
 * (Dev: skip when ``VITE_AUTH_DISABLED`` so local testing without login still works.)
 *
 * Real protection is on the API (JWT on every request). The SPA cannot make that tamper-proof;
 * this prevents casual URL/bookmark tricks from reusing a leftover client token after refresh.
 */
if (import.meta.env.VITE_AUTH_DISABLED !== "true") {
  clearAccessToken();
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
