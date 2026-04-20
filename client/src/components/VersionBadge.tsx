import { useState, useEffect } from "react";
import { isElectron } from "../electron";

export function VersionBadge() {
  const [updateVersion, setUpdateVersion] = useState<string | null>(null);
  const [updateReady, setUpdateReady] = useState(false);

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

  const outdated = !!updateVersion;

  return (
    <div style={{
      padding: "2px 12px",
      fontSize: "11px",
      fontFamily: "monospace",
      color: outdated ? "#b45309" : "#6b7a8d",
      textAlign: "right",
      background: outdated ? "#fffbeb" : undefined,
    }}>
      <span>v{__APP_VERSION__}</span>
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
