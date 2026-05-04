import { useCallback, useRef, useState } from "react";
import type { HomeTileFlags } from "../api/auth";
import { releaseAutomationBrowsers } from "../api/releaseBrowsers";
import { HomePageWatermark } from "../components/HomePageWatermark";
import "./HomePage.css";

interface HomePageProps {
  tiles: HomeTileFlags;
  /** Admin Saathi — ``roles_ref.admin_flag`` (JWT ``admin``). */
  showAdmin: boolean;
  onSelectPos: () => void;
  onSelectService: () => void;
  onSelectRto: () => void;
  onSelectDealer: () => void;
  onSelectAdmin: () => void;
}

export function HomePage({
  tiles,
  showAdmin,
  onSelectPos,
  onSelectService,
  onSelectRto,
  onSelectDealer,
  onSelectAdmin,
}: HomePageProps) {
  const [releaseBusy, setReleaseBusy] = useState(false);
  const [releaseHint, setReleaseHint] = useState<string | null>(null);
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const anyMainTile =
    tiles.tile_pos || tiles.tile_rto || tiles.tile_service || tiles.tile_dealer;
  const anyTile = anyMainTile || showAdmin;

  const onReleaseBrowsers = useCallback(async () => {
    if (hintTimerRef.current) {
      clearTimeout(hintTimerRef.current);
      hintTimerRef.current = null;
    }
    setReleaseBusy(true);
    setReleaseHint(null);
    try {
      const { ok, detail } = await releaseAutomationBrowsers();
      setReleaseHint(
        detail ? detail : ok ? "Automation browsers released." : "Release failed.",
      );
    } catch (e) {
      setReleaseHint(e instanceof Error ? e.message : String(e));
    } finally {
      setReleaseBusy(false);
      hintTimerRef.current = setTimeout(() => {
        setReleaseHint(null);
        hintTimerRef.current = null;
      }, 14000);
    }
  }, []);

  return (
    <div className="home-page">
      <div className="home-page-tiles-wrap">
        <HomePageWatermark />
        {!anyTile ? (
          <p className="home-page-no-tiles" role="status">
            No home modules are assigned for your role. Ask an administrator to set flags in{" "}
            <strong>roles_ref</strong> (POS, RTO, Service, Dealer, Admin).
          </p>
        ) : (
          <div className="home-tiles-layout">
            {showAdmin ? (
              <button
                type="button"
                className="home-tile home-tile-admin"
                onClick={onSelectAdmin}
                aria-label="Open Admin Saathi"
              >
                <span className="home-tile-title">Admin Saathi</span>
              </button>
            ) : null}

            {anyMainTile ? (
              <div className="home-tiles-grid" aria-label="Main modules">
                {tiles.tile_pos ? (
                  <button
                    type="button"
                    className="home-tile"
                    onClick={onSelectPos}
                    aria-label="Open Sales Window"
                  >
                    <span className="home-tile-title">Sales Window</span>
                    <span className="home-tile-desc">Easy DMS, Insurance and RTO</span>
                  </button>
                ) : null}
                {tiles.tile_rto ? (
                  <button
                    type="button"
                    className="home-tile"
                    onClick={onSelectRto}
                    aria-label="Open RTO Desk"
                  >
                    <span className="home-tile-title">RTO Desk</span>
                    <span className="home-tile-desc">Queue and track RTO work</span>
                  </button>
                ) : null}
                {tiles.tile_service ? (
                  <button
                    type="button"
                    className="home-tile home-tile-service"
                    onClick={onSelectService}
                    aria-label="Open Service Saathi"
                  >
                    <span className="home-tile-title">Service Saathi</span>
                    <span className="home-tile-desc">
                      Increase service usage through automatic reminders.
                    </span>
                  </button>
                ) : null}
                {tiles.tile_dealer ? (
                  <button
                    type="button"
                    className="home-tile"
                    onClick={onSelectDealer}
                    aria-label="Open Dealer Saathi"
                  >
                    <span className="home-tile-title">Dealer Saathi</span>
                    <span className="home-tile-desc">RTO details, Sub-dealer sales etc.</span>
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        )}
      </div>
      <div className="home-release-browsers-wrap">
        <button
          type="button"
          className="app-button app-button--small home-release-browsers-btn"
          disabled={releaseBusy}
          onClick={() => void onReleaseBrowsers()}
          title="Use if you closed the automation browser during a run and Retry or other actions stay stuck."
        >
          {releaseBusy ? "Releasing…" : "Release Browsers"}
        </button>
        {releaseHint ? (
          <span className="home-release-browsers-hint" role="status">
            {releaseHint}
          </span>
        ) : null}
      </div>
    </div>
  );
}
