import L from 'leaflet'

/** A map pin drawn as SVG rather than Leaflet's default icon.
 *
 *  Leaflet's default marker points at `marker-icon.png` through a URL it derives from the stylesheet's location. Under
 *  a bundler that file is fingerprinted and moved, so the request 404s and the browser renders a broken-image box with
 *  the alt text "Marker" — which is what the address picker was showing. Drawing the pin inline removes the asset (and
 *  the retina/shadow variants) from the problem entirely, and keeps it on the app's own palette.
 */
export function dropPin(color = '#2563eb'): L.DivIcon {
  return L.divIcon({
    className: 'drop-pin',
    iconSize: [28, 38],
    iconAnchor: [14, 36],      // the tip, not the middle, sits on the chosen coordinate
    popupAnchor: [0, -34],
    html: `
      <svg width="28" height="38" viewBox="0 0 28 38" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <ellipse cx="14" cy="35" rx="5" ry="2" fill="rgba(17,24,39,.28)"/>
        <path d="M14 1.5c-5.8 0-10.5 4.6-10.5 10.3 0 7.4 9.1 17.4 9.5 17.8a1.4 1.4 0 0 0 2 0c.4-.4 9.5-10.4 9.5-17.8C24.5 6.1 19.8 1.5 14 1.5z"
              fill="${color}" stroke="#fff" stroke-width="2.2"/>
        <circle cx="14" cy="11.8" r="3.6" fill="#fff"/>
      </svg>`,
  })
}
