import "./HomePage.css";

interface HomePageProps {
  onSelectPos: () => void;
  onSelectService: () => void;
  onSelectRto: () => void;
}

export function HomePage({ onSelectPos, onSelectService, onSelectRto }: HomePageProps) {
  return (
    <div className="home-page">
      <div className="home-page-tiles-wrap">
        <div className="home-page-watermark" aria-hidden />
        <div className="home-tiles">
        <button
          type="button"
          className="home-tile"
          onClick={onSelectPos}
          aria-label="Open POS Saathi"
        >
          <span className="home-tile-title">POS Saathi</span>
          <span className="home-tile-desc">Easy DMS, Insurance and RTO</span>
        </button>
        <button
          type="button"
          className="home-tile"
          onClick={onSelectRto}
          aria-label="Open RTO Payment Saathi"
        >
          <span className="home-tile-title">RTO Payment Saathi</span>
          <span className="home-tile-desc">One click payments</span>
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
      </div>
      </div>
    </div>
  );
}
