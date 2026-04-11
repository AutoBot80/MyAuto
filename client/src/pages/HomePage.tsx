import "./HomePage.css";

interface HomePageProps {
  onSelectPos: () => void;
  onSelectService: () => void;
  onSelectRto: () => void;
  onSelectDealer: () => void;
  onSelectAdmin: () => void;
}

export function HomePage({ onSelectPos, onSelectService, onSelectRto, onSelectDealer, onSelectAdmin }: HomePageProps) {
  return (
    <div className="home-page">
      <div className="home-page-tiles-wrap">
        <div className="home-page-watermark" aria-hidden />
        <div className="home-tiles-layout">
          <button
            type="button"
            className="home-tile home-tile-admin"
            onClick={onSelectAdmin}
            aria-label="Open Admin Saathi"
          >
            <span className="home-tile-title">Admin Saathi</span>
          </button>

          <div className="home-tiles-grid" aria-label="Main modules">
            <button
              type="button"
              className="home-tile"
              onClick={onSelectPos}
              aria-label="Open Sales Window"
            >
              <span className="home-tile-title">Sales Window</span>
              <span className="home-tile-desc">Easy DMS, Insurance and RTO</span>
            </button>
            <button
              type="button"
              className="home-tile"
              onClick={onSelectRto}
              aria-label="Open RTO Desk"
            >
              <span className="home-tile-title">RTO Desk</span>
              <span className="home-tile-desc">Queue and track RTO work</span>
            </button>
            <button
              type="button"
              className="home-tile home-tile-service"
              onClick={onSelectService}
              aria-label="Open Service Saathi"
            >
              <span className="home-tile-title">Service Saathi</span>
              <span className="home-tile-desc">Increase service usage through automatic reminders.</span>
            </button>
            <button
              type="button"
              className="home-tile"
              onClick={onSelectDealer}
              aria-label="Open Dealer Saathi"
            >
              <span className="home-tile-title">Dealer Saathi</span>
              <span className="home-tile-desc">RTO details, Sub-dealer sales etc.</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
