import "./HomePage.css";

interface HomePageProps {
  onSelectPos: () => void;
  onSelectService: () => void;
}

export function HomePage({ onSelectPos, onSelectService }: HomePageProps) {
  return (
    <div className="home-page">
      <div className="home-tiles">
        <button
          type="button"
          className="home-tile"
          onClick={onSelectPos}
          aria-label="Open POS Saathi"
        >
          <span className="home-tile-title">POS Saathi</span>
          <span className="home-tile-desc">Sales, customers, DMS, insurance, RTO</span>
        </button>
        <button
          type="button"
          className="home-tile"
          onClick={onSelectService}
          aria-label="Open Service Saathi"
        >
          <span className="home-tile-title">Service Saathi</span>
          <span className="home-tile-desc">Service reminders</span>
        </button>
      </div>
    </div>
  );
}
