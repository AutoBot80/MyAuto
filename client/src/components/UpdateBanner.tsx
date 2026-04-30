import { useState, useEffect } from "react";
import { isElectron } from "../electron";

type Phase = "idle" | "downloading" | "ready";

export function UpdateBanner() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [version, setVersion] = useState<string>("");
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (!isElectron()) return;
    const api = window.electronAPI!.updater;

    api.onAvailable((info: unknown) => {
      const v = (info as { version?: string })?.version ?? "";
      setVersion(v);
      setPhase("downloading");
      setDismissed(false);
    });

    api.onDownloaded((_info: unknown) => {
      setPhase("ready");
      setDismissed(false);
    });

    api.onError((_msg: string) => {
      setPhase("idle");
    });
  }, []);

  if (!isElectron() || phase === "idle" || dismissed) return null;

  return (
    <div style={bannerStyle}>
      {phase === "downloading" && (
        <span>Downloading update{version ? ` v${version}` : ""}...</span>
      )}
      {phase === "ready" && (
        <>
          <span>
            Update ready{version ? ` (v${version})` : ""}. It will install the next time you start the app.
          </span>
          <button
            type="button"
            onClick={() => window.electronAPI!.updater.install()}
            style={btnStyle}
          >
            Restart now
          </button>
        </>
      )}
      <button
        type="button"
        onClick={() => setDismissed(true)}
        style={dismissStyle}
        aria-label="Dismiss"
      >
        &times;
      </button>
    </div>
  );
}

const bannerStyle: React.CSSProperties = {
  position: "fixed",
  bottom: 0,
  left: 0,
  right: 0,
  zIndex: 9999,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "12px",
  padding: "10px 20px",
  background: "#1a56db",
  color: "#fff",
  fontSize: "14px",
  fontFamily: "Segoe UI, system-ui, sans-serif",
};

const btnStyle: React.CSSProperties = {
  background: "#fff",
  color: "#1a56db",
  border: "none",
  borderRadius: "4px",
  padding: "5px 14px",
  fontWeight: 600,
  cursor: "pointer",
  fontSize: "13px",
};

const dismissStyle: React.CSSProperties = {
  position: "absolute",
  right: "12px",
  background: "none",
  border: "none",
  color: "#fff",
  fontSize: "20px",
  cursor: "pointer",
  lineHeight: 1,
};
