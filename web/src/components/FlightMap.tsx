import "leaflet/dist/leaflet.css";
import { useState } from "react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer } from "react-leaflet";
import type {
  AgentConfig,
  AirportInsight,
  DowInsight,
  InsightsFile,
  LatestFile,
  LatestOffer,
  RouteInfo,
  RoutesFile,
  StatsFile,
} from "@/types/data";
import { cn } from "@/lib/utils";
import { airlineNames } from "@/lib/airlines";

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

// ---- Deal rate bar ----------------------------------------------------------
function DealRateBar({ pct }: { pct: number | null }) {
  const val = pct ?? 0;
  const color = val >= 40 ? "#10b981" : val >= 20 ? "#f59e0b" : "#ef4444";
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-gray-200">
        <div style={{ width: `${Math.min(val, 100)}%`, background: color }} className="h-full rounded-full" />
      </div>
      <span className="text-xs tabular-nums">{val.toFixed(0)}%</span>
    </div>
  );
}

// ---- Sort helper types -------------------------------------------------------
type SortDir = "asc" | "desc";
type AirportSortCol = "code" | "dealRate" | "median" | "effective" | "observations";

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  return (
    <span className={`ml-0.5 inline-block text-[10px] leading-none ${active ? "text-gray-800" : "text-gray-300"}`}>
      {active && dir === "asc" ? "▲" : "▼"}
    </span>
  );
}

// ---- Airport insights table -------------------------------------------------
function AirportInsightsTable({
  title,
  airports,
  transportByCode,
  color,
  group,
  agentConfig,
  onToggleAirport,
}: {
  title: string;
  airports: AirportInsight[];
  transportByCode?: Record<string, number>;
  color: string;
  group?: "europeAirports" | "japanAirports";
  agentConfig?: AgentConfig | null;
  onToggleAirport?: (code: string, group: "europeAirports" | "japanAirports", enabled: boolean) => void;
}) {
  const hasTransport = !!transportByCode && Object.keys(transportByCode).length > 0;
  const [sort, setSort] = useState<{ col: AirportSortCol; dir: SortDir }>({
    col: "dealRate",
    dir: "desc",
  });

  function toggleSort(col: AirportSortCol) {
    setSort((prev) =>
      prev.col === col
        ? { col, dir: prev.dir === "desc" ? "asc" : "desc" }
        : { col, dir: col === "code" || col === "median" || col === "effective" ? "asc" : "desc" },
    );
  }

  const enriched = airports.map((ap) => {
    const transport = transportByCode?.[ap.code];
    const effectivePrice =
      ap.medianEur != null && transport != null ? ap.medianEur + transport : null;
    const configAp = group && agentConfig ? agentConfig[group].find((a) => a.code === ap.code || a.cityCode === ap.code) : null;
    return { ap, effectivePrice, configAp };
  });

  const sorted = [...enriched].sort((a, b) => {
    const mul = sort.dir === "asc" ? 1 : -1;
    switch (sort.col) {
      case "code":
        return mul * a.ap.code.localeCompare(b.ap.code);
      case "dealRate":
        return mul * ((a.ap.dealRatePct ?? -1) - (b.ap.dealRatePct ?? -1));
      case "median":
        return mul * ((a.ap.medianEur ?? Infinity) - (b.ap.medianEur ?? Infinity));
      case "effective":
        return mul * ((a.effectivePrice ?? a.ap.medianEur ?? Infinity) - (b.effectivePrice ?? b.ap.medianEur ?? Infinity));
      case "observations":
        return mul * (a.ap.observations - b.ap.observations);
    }
  });

  function Th({ col, label, right }: { col: AirportSortCol; label: string; right?: boolean }) {
    const active = sort.col === col;
    return (
      <th
        className={`cursor-pointer select-none pb-1 pr-3 font-medium hover:text-gray-800 ${right ? "text-right" : ""} ${active ? "text-gray-700" : ""}`}
        onClick={() => toggleSort(col)}
      >
        {label}
        <SortIcon active={active} dir={sort.dir} />
      </th>
    );
  }

  const showToggleCol = !!(group && agentConfig && onToggleAirport);

  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-gray-700">{title}</h3>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <Th col="code" label="Letiště" />
            <Th col="dealRate" label="Podíl dealů" />
            <Th col="median" label="Medián" right />
            {hasTransport && <Th col="effective" label="vč. dopravy" right />}
            <Th col="observations" label="Pozorování" right />
            {showToggleCol && <th className="pb-1 text-right font-medium">Aktivní</th>}
          </tr>
        </thead>
        <tbody>
          {sorted.map(({ ap, effectivePrice, configAp }, i) => {
            const isBest = i === 0;
            const isEnabled = configAp?.enabled ?? true;
            return (
              <tr
                key={ap.code}
                className={cn(
                  "border-b border-gray-100",
                  isBest ? "font-semibold" : "",
                  configAp && !isEnabled ? "opacity-50" : "",
                )}
              >
                <td className="py-1 pr-3">
                  <span style={{ color }} className="mr-1 font-bold">{ap.code}</span>
                  {isBest && <span className="text-green-600">★</span>}
                </td>
                <td className="py-1 pr-3">
                  <DealRateBar pct={ap.dealRatePct} />
                </td>
                <td className="py-1 pr-3 text-right tabular-nums">
                  {ap.medianEur != null ? `${ap.medianEur.toFixed(0)} €` : "—"}
                </td>
                {hasTransport && (
                  <td className="py-1 pr-3 text-right tabular-nums text-gray-500">
                    {effectivePrice != null ? `${effectivePrice.toFixed(0)} €` : "—"}
                  </td>
                )}
                <td className="py-1 pr-3 text-right tabular-nums text-gray-400">{ap.observations}</td>
                {showToggleCol && (
                  <td className="py-1 text-right">
                    {configAp ? (
                      <button
                        onClick={() => onToggleAirport!(ap.code, group!, !isEnabled)}
                        className={cn(
                          "relative inline-flex h-5 w-9 rounded-full transition-colors",
                          isEnabled ? "bg-emerald-500" : "bg-gray-300",
                        )}
                        title={isEnabled ? "Deaktivovat" : "Aktivovat"}
                      >
                        <span className={cn(
                          "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all",
                          isEnabled ? "left-4" : "left-0.5",
                        )} />
                      </button>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---- Day-of-week table ------------------------------------------------------
function DowTable({
  title,
  rows,
  minTransportCost,
}: {
  title: string;
  rows: DowInsight[];
  minTransportCost?: number;
}) {
  const [activeDow, setActiveDow] = useState<Set<string>>(() => new Set(rows.map((r) => r.dow)));
  const bestDealRate = Math.max(...rows.map((r) => r.dealRatePct ?? 0));

  function toggleDow(dow: string) {
    setActiveDow((prev) => {
      const next = new Set(prev);
      if (next.has(dow)) next.delete(dow);
      else next.add(dow);
      return next;
    });
  }

  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-gray-700">{title}</h3>
      <div className="flex flex-wrap gap-2">
        {rows.map((row) => {
          const isTopDay = (row.dealRatePct ?? 0) === bestDealRate && bestDealRate > 0;
          const isActive = activeDow.has(row.dow);
          const rate = row.dealRatePct ?? 0;
          const barColor = rate >= 40 ? "#10b981" : rate >= 20 ? "#f59e0b" : "#9ca3af";
          const effectivePrice =
            row.medianEur != null && minTransportCost != null
              ? row.medianEur + minTransportCost
              : row.medianEur;
          return (
            <div
              key={row.dow}
              onClick={() => toggleDow(row.dow)}
              className={cn(
                "flex min-w-[76px] cursor-pointer flex-col items-center rounded-lg border px-2 py-2 text-center transition-opacity select-none",
                isTopDay ? "border-green-400 bg-green-50" : "border-gray-200 bg-gray-50",
                isActive ? "opacity-100" : "opacity-35",
              )}
              title={isActive ? "Kliknutím deaktivovat" : "Kliknutím aktivovat"}
            >
              <span className="text-sm font-bold text-gray-800">
                {row.dow}
                {isTopDay && <span className="ml-0.5 text-green-600">★</span>}
              </span>
              <div className="my-1 h-1.5 w-14 overflow-hidden rounded-full bg-gray-200">
                <div style={{ width: `${Math.min(rate, 100)}%`, background: barColor }} className="h-full rounded-full" />
              </div>
              <span className="text-xs tabular-nums text-gray-500">{rate.toFixed(0)}% dealů</span>
              {effectivePrice != null && (
                <span className="text-xs font-semibold tabular-nums text-gray-800">
                  {effectivePrice.toFixed(0)} €
                </span>
              )}
              {minTransportCost != null && row.medianEur != null && (
                <span className="text-[10px] tabular-nums text-gray-400">
                  let {row.medianEur.toFixed(0)} €
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---- Insights panel ---------------------------------------------------------
function InsightsPanel({
  insights,
  agentConfig,
  onToggleAirport,
}: {
  insights: InsightsFile;
  agentConfig: AgentConfig | null;
  onToggleAirport?: (code: string, group: "europeAirports" | "japanAirports", enabled: boolean) => void;
}) {
  // totalRoundtripTransport: celkové náklady na dopravu tam i zpět
  // pro letišťa s feeder letem: zpáteční letenka + 2× vlak na hub
  // pro ostatní: 2× cena jedné cesty
  const transportByCode: Record<string, number> = {};
  if (agentConfig) {
    for (const ap of agentConfig.europeAirports) {
      const t = ap.transport;
      if (t?.costEur != null) {
        const total =
          t.mode === "let"
            ? (t.costEurRoundtrip ?? t.costEur * 2) + 2 * (t.airportTransferCostEur ?? 25)
            : 2 * t.costEur;
        transportByCode[ap.code] = total;
        if (ap.cityCode) transportByCode[ap.cityCode] = total;
      }
    }
  }
  const transportValues = Object.values(transportByCode);
  const minTransportCost = transportValues.length > 0 ? Math.min(...transportValues) : undefined;

  return (
    <div className="mt-4 space-y-6 rounded-lg border bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-gray-800">
        Statistiky letišť a termínů
      </h2>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <AirportInsightsTable
          title="Evropská odletová letiště — podíl dealů"
          airports={insights.airportPriority.europe}
          transportByCode={Object.keys(transportByCode).length ? transportByCode : undefined}
          color="#1d4ed8"
          group="europeAirports"
          agentConfig={agentConfig}
          onToggleAirport={onToggleAirport}
        />
        <AirportInsightsTable
          title="Japonská cílová letiště — podíl dealů"
          airports={insights.airportPriority.japan}
          color="#b45309"
          group="japanAirports"
          agentConfig={agentConfig}
          onToggleAirport={onToggleAirport}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <DowTable
          title="Nejlevnější dny odletu"
          rows={insights.cheapestDepartureDow}
          minTransportCost={minTransportCost}
        />
        <DowTable
          title="Nejlevnější dny návratu"
          rows={insights.cheapestArrivalDow}
          minTransportCost={minTransportCost}
        />
      </div>

      {Object.keys(transportByCode).length > 0 && (
        <p className="text-xs text-gray-400">
          „vč. dopravy" = medián letenky + celková doprava tam i zpět
          (z&nbsp;{agentConfig?.homeLocation}). Dny: přepočteno na nejlevnější dostupné letiště
          ({Math.min(...Object.values(transportByCode))} € celkem).
        </p>
      )}
    </div>
  );
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
              {offer.airlines.length > 0 ? ` · ${airlineNames(offer.airlines)}` : ` · ${offer.source}`}
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
  insights: InsightsFile | null;
  agentConfig: AgentConfig | null;
  onSelectRoute: (routeKey: string) => void;
  showMap?: boolean;
  showInsights?: boolean;
  onToggleAirport?: (code: string, group: "europeAirports" | "japanAirports", enabled: boolean) => void;
}

export function FlightMap({ routes, latest, stats, insights, agentConfig, onSelectRoute, showMap = true, showInsights = true, onToggleAirport }: Props) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const insightsNode = showInsights && insights ? (
    <InsightsPanel insights={insights} agentConfig={agentConfig} onToggleAirport={onToggleAirport} />
  ) : null;

  if (!showMap) {
    return <div className="space-y-4">{insightsNode}</div>;
  }

  if (!routes || routes.length === 0) {
    return (
      <div className="space-y-4">
        <div className="flex h-[520px] items-center justify-center rounded-lg border bg-muted/30 text-sm text-muted-foreground">
          Mapová data nejsou k dispozici.
        </div>
        {insightsNode}
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
    <div className="space-y-4">
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

      {insightsNode}
    </div>
  );
}
