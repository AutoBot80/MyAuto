import watermarkUrl from "../assets/watermark.png";

/**
 * Login + home: Bloom image behind center content. Import (not CSS url) so the built URL
 * is correct for Electron's file:// + Vite `base: './'`.
 */
export function HomePageWatermark() {
  return (
    <img
      className="home-page-watermark"
      src={watermarkUrl}
      alt=""
      aria-hidden
      draggable={false}
    />
  );
}
