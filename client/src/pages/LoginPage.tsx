import { useEffect, useMemo, useState } from "react";
import { AppChrome } from "../components/AppChrome";
import { useToday } from "../hooks/useToday";
import { loginApi, type LoginRequest, type LoginResponse } from "../api/auth";
import "./HomePage.css";
import "./LoginPage.css";

type Props = {
  onLoggedIn: (session: LoginResponse) => void;
};

export function LoginPage({ onLoggedIn }: Props) {
  const today = useToday();
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** After API reports multiple dealers for this login — user picks one and retries. */
  const [needsDealerChoice, setNeedsDealerChoice] = useState(false);
  const [multiDealerId, setMultiDealerId] = useState("");

  useEffect(() => {
    setNeedsDealerChoice(false);
    setMultiDealerId("");
  }, [loginId]);

  const resolvedMultiDealerId = useMemo(() => {
    const n = parseInt(multiDealerId.trim(), 10);
    return Number.isFinite(n) && n >= 1 ? n : null;
  }, [multiDealerId]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!loginId.trim() || !password) {
      setError("Login ID and password are required.");
      return;
    }
    const lid = loginId.trim();

    const payload: LoginRequest = { login_id: lid, password };

    if (needsDealerChoice) {
      if (resolvedMultiDealerId === null) {
        setError("Enter a valid dealer ID.");
        return;
      }
      payload.dealer_id = resolvedMultiDealerId;
    }

    setBusy(true);
    try {
      const session = await loginApi(payload);
      onLoggedIn(session);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Login failed.";
      setError(msg);
      if (/multiple dealers/i.test(msg)) {
        setNeedsDealerChoice(true);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppChrome>
      <div className="app-wrap app-wrap-v2">
        <div className="app-box">
          <header className="app-topbar">
            <div className="app-topbar-left" />
            <div className="app-topbar-spacer" />
            <div className="app-topbar-title-block">
              <h1 className="app-topbar-title login-topbar-welcome">
                Welcome to Dealer Saathi
                <sup className="login-topbar-tm">™</sup>
              </h1>
            </div>
            <div className="app-topbar-spacer" />
            <div className="app-topbar-right-with-home">
              <span className="app-topbar-brand">© Dealer Saathi ™</span>
              <div className="app-topbar-date">
                <span>{today}</span>
              </div>
            </div>
          </header>
          <main className="app-main-v2">
            <div className="home-page login-page">
              <div className="home-page-tiles-wrap">
                <div className="home-page-watermark" aria-hidden />
                <div className="login-page-content">
                  <div className="login-card">
                    <div className="login-intro">
                      <p className="login-hint">Please Sign in with your details.</p>
                    </div>
                    <form className="login-form" onSubmit={onSubmit}>
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
                      {needsDealerChoice ? (
                        <label className="login-label">
                          Dealer ID
                          <input
                            className="login-input"
                            type="number"
                            min={1}
                            value={multiDealerId}
                            onChange={(e) => setMultiDealerId(e.target.value)}
                            autoComplete="off"
                            placeholder="Required — your account has more than one dealer"
                          />
                        </label>
                      ) : null}
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
                      <button
                        className="app-button app-button--primary login-submit"
                        type="submit"
                        disabled={busy || (needsDealerChoice && resolvedMultiDealerId === null)}
                      >
                        {busy ? "Signing in…" : "Sign in"}
                      </button>
                    </form>
                  </div>
                </div>
              </div>
            </div>
          </main>
        </div>
      </div>
    </AppChrome>
  );
}
