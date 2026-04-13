import { useState } from "react";
import { loginApi, type LoginResponse } from "../api/auth";
import "./LoginPage.css";

type Props = {
  onLoggedIn: (session: LoginResponse) => void;
};

export function LoginPage({ onLoggedIn }: Props) {
  const [dealerId, setDealerId] = useState(
    () => String(Number(import.meta.env.VITE_DEALER_ID) || 100001),
  );
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const did = parseInt(dealerId.trim(), 10);
    if (!Number.isFinite(did) || did < 1) {
      setError("Enter a valid dealer ID.");
      return;
    }
    if (!loginId.trim() || !password) {
      setError("Login ID and password are required.");
      return;
    }
    setBusy(true);
    try {
      const session = await loginApi({
        dealer_id: did,
        login_id: loginId.trim(),
        password,
      });
      onLoggedIn(session);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Login failed.";
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">Dealer Saathi</h1>
        <p className="login-hint">Sign in with your dealer ID and credentials.</p>
        <form className="login-form" onSubmit={onSubmit}>
          <label className="login-label">
            Dealer ID
            <input
              className="login-input"
              type="number"
              min={1}
              value={dealerId}
              onChange={(e) => setDealerId(e.target.value)}
              autoComplete="username"
            />
          </label>
          <label className="login-label">
            Login ID
            <input
              className="login-input"
              type="text"
              value={loginId}
              onChange={(e) => setLoginId(e.target.value)}
              autoComplete="username"
            />
          </label>
          <label className="login-label">
            Password
            <input
              className="login-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          {error ? <p className="login-error">{error}</p> : null}
          <button className="login-submit" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
