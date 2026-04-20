import { useState, useEffect } from "react";
import { fetchHealth, getBaseUrl } from "../api/client";
import { getAccessToken } from "../auth/token";
import { isElectron } from "../electron";

export function VersionBadge() {
  const [updateVersion, setUpdateVersion] = useState<string | null>(null);
  const [updateReady, setUpdateReady] = useState(false);
  const [beVersion, setBeVersion] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then((h) => {
        if (!cancelled) setBeVersion(typeof h.version === "string" ? h.version : "--");
      })
      .catch(() => {
        if (!cancelled) setBeVersion("--");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isElectron()) return;
    const api = window.electronAPI!.updater;

    api.onAvailable((info: unknown) => {
      const v = (info as { version?: string })?.version ?? "";
      setUpdateVersion(v);
    });

    api.onDownloaded(() => {
      setUpdateReady(true);
    });
  }, []);

  useEffect(() => {
    if (!isElectron() || !window.electronAPI?.sidecar) return;
    const token = getAccessToken();
    if (!token) return;
    void window.electronAPI.sidecar
      .runJob({
        type: "warm_browser",
        api_url: getBaseUrl(),
        jwt: token,
        params: {},
      })
      .catch(() => {});
  }, []);

  const outdated = !!updateVersion;
  const fe = __APP_VERSION__;
  const be = beVersion === null ? "…" : beVersion;

  return (
    <div
      style={{
        padding: "2px 12px",
        fontSize: "11px",
        fontFamily: "monospace",
        color: outdated ? "#b45309" : "#6b7a8d",
        textAlign: "right",
        background: outdated ? "#fffbeb" : undefined,
      }}
    >
      <span>
        FE v{fe} | BE v{be}
      </span>
      {outdated && (
        <span style={{ marginLeft: 8, fontSize: "10px" }}>
          {updateReady
            ? `\u2022 v${updateVersion} ready \u2014 restart to update`
            : `\u2022 v${updateVersion} available`}
        </span>
      )}
    </div>
  );
}
