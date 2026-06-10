import "leaflet/dist/leaflet.css";
import { useState } from "react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer } from "react-leaflet";
import type {
  LatestFile,
  LatestOffer,
  RouteInfo,
  RoutesFile,
  StatsFile,
} from "@/types/data";

// ---- Great-circle arc (SLERP) -----------------------------------------------
function greatCirclePoints(
  lat1: number, lon1: number,
  lat2: number, lon2: number,
  n = 40,
): [number, number][] {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const toDeg = (r: number) => (r * 180) / Math.PI;
  const φ1 = toRad(lat1), λ1 = toRad(lon1);
  const φ2 = toRad(lat2), λ2 = toRad(lon2);
  const Δσ = 2 * Math.asin(Math.sqrt(
    Math.sin((φ2 - φ1) / 2) ** 2 +
    Math.cos(φ1) * Math.cos(φ2) * Math.sin((λ2 - λ1) / 2) ** 2,
  ));
  if (Δσ < 1e-9) return [[lat1, lon1]];
  return Array.from({ length: n + 1 }, (_, i) => {
    const f = i / n;
    const A = Math.sin((1 - f) * Δσ) / Math.sin(Δσ);
    const B = Math.sin(f * Δσ) / Math.sin(Δσ);
    const x = A * Math.cos(φ1) * Math.cos(λ1) + B * Math.cos(φ2) * Math.cos(λ2);
    const y = A * Math.cos(φ1) * Math.sin(λ1) + B * Math.cos(φ2) * Math.sin(λ2);
    const z = A * Math.sin(φ1) + B * Math.sin(φ2);
    return [toDeg(Math.atan2(z, Math.sqrt(x * x + y * y))), toDeg(Math.atan2(y, x))] as [number, number];
  });
}

// ---- Price quality ----------------------------------------------------------
function qualityColor(pct: number | null): string {
  if (pct === null) return "#9ca3af";
  if (pct <= -12) return "#10b981";
  if (pct <= -5) return "#84cc16";
  if (pct <= 5) return "#f59e0b";
  return "#ef4444";
}

function qualityLabel(pct: number | null): string {
  if (pct === null) return "bez dat";
  if (pct <= -12) return "výborná";
  if (pct <= -5) return "dobrá";
  if (pct <= 5) return "průměrná";
  return "nad průměrem";
}

// ---- Types ------------------------------------------------------------------
interface AirportMarker {
  code: string;
  name: string;
  lat: number;
  lon: number;
  region: "europe" | "japan";
}

interface RouteWithData {
  route: RouteInfo;
  offer: LatestOffer | undefined;
  color: string;
  qualityText: string;
  weight: number;
  isHighlight: boolean;
}

// ---- Popup content ----------------------------------------------------------
function RoutePopup({
  rd,
  onSelect,
}: {
  rd: RouteWithData;
  onSelect: () => void;
}) {
  const { route, offer, color, qualityText } = rd;
  return (
    <div style={{ minWidth: 210, lineHeight: 1.5 }}>
      <div style={{ fontWeight: 700, fontSize: "0.95em", marginBottom: 2 }}>
        {route.origin} → {route.destination}
        {route.returnOrigin ? ` / ${route.returnOrigin} → ${route.origin}` : ""}
      </div>
      <div style={{ color: "#6b7280", fontSize: "0.82em", marginBottom: 6 }}>
        {route.originName} → {route.destinationName}
        {route.returnOriginName ? ` / ${route.returnOriginName}` : ""}
      </div>
      {offer ? (
        <>
          <div style={{ fontWeight: 700, fontSize: "1.15em", color, marginBottom: 3 }}>
            {offer.price} €
            <span style={{ fontWeight: 400, fontSize: "0.78em", color: "#6b7280", marginLeft: 6 }}>
              {qualityText}
            </span>
          </div>
          {offer.departDate && (
            <div style={{ fontSize: "0.82em", color: "#555", marginBottom: 2 }}>
              Odlet: {offer.departDate}
              {offer.returnDate ? ` · Návrat: ${offer.returnDate}` : ""}
            </div>
          )}
          {offer.nights != null && (
            <div style={{ fontSize: "0.82em", color: "#555", marginBottom: 2 }}>
              {offer.nights} nocí
              {offer.airlines.length > 0 ? ` · ${offer.airlines.join(", ")}` : ` · ${offer.source}`}
            </div>
          )}
          {(offer.flags.isNewLow || offer.flags.isBigDrop) && (
            <div style={{ color: "#10b981", fontSize: "0.82em", marginTop: 4 }}>
              {offer.flags.isNewLow && "★ Nové historické minimum  "}
              {offer.flags.isBigDrop && "↓ Velký pokles"}
            </div>
          )}
        </>
      ) : (
        <div style={{ color: "#9ca3af", fontSize: "0.85em" }}>Žádná aktuální nabídka</div>
      )}
      <button
        onClick={onSelect}
        style={{
          marginTop: 10, padding: "4px 12px",
          background: "#3b82f6", color: "white",
          border: "none", borderRadius: 4,
          cursor: "pointer", fontSize: "0.82em", fontWeight: 600,
        }}
      >
        Detail trasy →
      </button>
    </div>
  );
}

// ---- Main component ---------------------------------------------------------
interface Props {
  routes: RoutesFile | null;
  latest: LatestFile | null;
  stats: StatsFile | null;
  onSelectRoute: (routeKey: string) => void;
}

export function FlightMap({ routes, latest, stats, onSelectRoute }: Props) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  if (!routes || routes.length === 0) {
    return (
      <div className="flex h-[520px] items-center justify-center rounded-lg border bg-muted/30 text-sm text-muted-foreground">
        Mapová data nejsou k dispozici.
      </div>
    );
  }

  // Enrich each route with offer + color
  const routesWithData: RouteWithData[] = routes.map((route) => {
    const offer = (latest ?? []).find((o) => o.routeKey === route.routeKey);
    const pct = stats?.[route.routeKey]?.currentVsAvgPct ?? null;
    const isHighlight = !!(offer?.flags.isNewLow || offer?.flags.isBigDrop);
    return {
      route,
      offer,
      color: qualityColor(pct),
      qualityText: qualityLabel(pct),
      weight: selectedKey === route.routeKey ? 5 : isHighlight ? 3.5 : 2.5,
      isHighlight,
    };
  });

  // Unique airports
  const europeMap = new Map<string, AirportMarker>();
  const japanMap = new Map<string, AirportMarker>();
  for (const { route } of routesWithData) {
    if (route.coords.origin) {
      europeMap.set(route.origin, { code: route.origin, name: route.originName, lat: route.coords.origin.lat, lon: route.coords.origin.lon, region: "europe" });
    }
    if (route.coords.destination) {
      japanMap.set(route.destination, { code: route.destination, name: route.destinationName, lat: route.coords.destination.lat, lon: route.coords.destination.lon, region: "japan" });
    }
    if (route.returnOrigin && route.coords.returnOrigin && route.returnOriginName) {
      japanMap.set(route.returnOrigin, { code: route.returnOrigin, name: route.returnOriginName, lat: route.coords.returnOrigin.lat, lon: route.coords.returnOrigin.lon, region: "japan" });
    }
  }

  return (
    <div className="relative overflow-hidden rounded-lg border shadow-sm">
      <MapContainer
        center={[45, 72]}
        zoom={3}
        style={{ height: 540, width: "100%" }}
        scrollWheelZoom
        minZoom={2}
        maxZoom={9}
      >
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
          attribution='© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/attributions">CARTO</a>'
        />

        {/* Outbound arcs */}
        {routesWithData.map((rd) => {
          const { route, color, weight } = rd;
          const orig = route.coords.origin;
          const dest = route.coords.destination;
          if (!orig || !dest) return null;
          const isSelected = selectedKey === route.routeKey;
          return (
            <Polyline
              key={route.routeKey}
              positions={greatCirclePoints(orig.lat, orig.lon, dest.lat, dest.lon)}
              pathOptions={{
                color,
                weight,
                opacity: isSelected ? 1 : 0.78,
                dashArray: route.type === "openjaw" ? "8 5" : undefined,
              }}
              eventHandlers={{
                click: () => setSelectedKey(isSelected ? null : route.routeKey),
              }}
            >
              <Popup minWidth={220}>
                <RoutePopup rd={rd} onSelect={() => onSelectRoute(route.routeKey)} />
              </Popup>
            </Polyline>
          );
        })}

        {/* Return leg of open-jaw (dashed, lighter) */}
        {routesWithData.map(({ route, color }) => {
          if (route.type !== "openjaw") return null;
          const retOrig = route.coords.returnOrigin;
          const orig = route.coords.origin;
          if (!retOrig || !orig) return null;
          return (
            <Polyline
              key={`${route.routeKey}-ret`}
              positions={greatCirclePoints(retOrig.lat, retOrig.lon, orig.lat, orig.lon)}
              pathOptions={{ color, weight: 1.5, opacity: 0.4, dashArray: "4 7" }}
              interactive={false}
            />
          );
        })}

        {/* European airport markers */}
        {Array.from(europeMap.values()).map((ap) => (
          <CircleMarker
            key={`eu-${ap.code}`}
            center={[ap.lat, ap.lon]}
            radius={7}
            pathOptions={{ color: "#1d4ed8", fillColor: "#3b82f6", fillOpacity: 0.9, weight: 1.5 }}
          >
            <Popup>
              <strong>{ap.code}</strong>
              <br />
              <span style={{ fontSize: "0.85em", color: "#555" }}>{ap.name}</span>
            </Popup>
          </CircleMarker>
        ))}

        {/* Japanese airport markers */}
        {Array.from(japanMap.values()).map((ap) => (
          <CircleMarker
            key={`jp-${ap.code}`}
            center={[ap.lat, ap.lon]}
            radius={7}
            pathOptions={{ color: "#b45309", fillColor: "#f97316", fillOpacity: 0.9, weight: 1.5 }}
          >
            <Popup>
              <strong>{ap.code}</strong>
              <br />
              <span style={{ fontSize: "0.85em", color: "#555" }}>{ap.name}</span>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>

      {/* Floating legend */}
      <div
        className="absolute bottom-6 left-4 z-[1000] rounded-lg bg-white/95 px-3 py-2.5 text-xs shadow-md backdrop-blur-sm"
        style={{ pointerEvents: "none" }}
      >
        <div className="mb-1.5 font-semibold text-gray-700">Cena vs. průměr 90 dní</div>
        {[
          { color: "#10b981", label: "≤ −12 % — výborná" },
          { color: "#84cc16", label: "−12 % až −5 % — dobrá" },
          { color: "#f59e0b", label: "±5 % — průměrná" },
          { color: "#ef4444", label: "> +5 % — drahá" },
          { color: "#9ca3af", label: "bez dat" },
        ].map(({ color, label }) => (
          <div key={label} className="flex items-center gap-1.5 py-px">
            <span style={{ display: "inline-block", width: 18, height: 3, background: color, borderRadius: 2, flexShrink: 0 }} />
            <span className="text-gray-600">{label}</span>
          </div>
        ))}
        <div className="mt-2 space-y-0.5 border-t pt-1.5 text-gray-500">
          <div className="flex items-center gap-1.5">
            <span style={{ display: "inline-block", width: 18, height: 2, borderTop: "2px solid #6b7280", borderRadius: 0 }} />
            <span>zpáteční let</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span style={{ display: "inline-block", width: 18, height: 2, borderTop: "2px dashed #6b7280" }} />
            <span>open-jaw</span>
          </div>
        </div>
        <div className="mt-1.5 flex gap-3 text-gray-500">
          <span className="flex items-center gap-1">
            <span style={{ display: "inline-block", width: 9, height: 9, borderRadius: "50%", background: "#3b82f6" }} />
            <span>EU letiště</span>
          </span>
          <span className="flex items-center gap-1">
            <span style={{ display: "inline-block", width: 9, height: 9, borderRadius: "50%", background: "#f97316" }} />
            <span>JP letiště</span>
          </span>
        </div>
      </div>
    </div>
  );
}
