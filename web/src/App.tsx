import { useEffect, useState } from "react";
import { FlightMap } from "@/components/FlightMap";
import { EMPTY_FILTERS, type OfferFilters } from "@/components/FilterBar";
import { RouteDetailView } from "@/components/RouteDetailView";
import { SettingsPage } from "@/components/SettingsPage";
import { SwimlanesView } from "@/components/SwimlanesView";
import { useDataLoader } from "@/hooks/useDataLoader";
import { cloneConfig, serializeConfig } from "@/lib/agentConfig";
import { commitWithRetry, loadToken } from "@/lib/github";
import { HomePage } from "@/pages/HomePage";
import type { AgentConfig, LatestFile } from "@/types/data";

type AppView = "offers" | "swimlanes" | "map" | "settings";

const TAB_LABELS: Record<AppView, string> = {
  offers: "Nabídky",
  swimlanes: "Časová osa",
  map: "Statistika",
  settings: "Nastavení",
};

export default function App() {
  const [view, setView] = useState<AppView>("offers");
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null);
  const { latest, stats, agentConfig, routes, insights, loading, error } = useDataLoader();
  const [localConfig, setLocalConfig] = useState<AgentConfig | null>(null);
  const [filteredOffers, setFilteredOffers] = useState<LatestFile | null>(null);
  const [filters, setFilters] = useState<OfferFilters>(EMPTY_FILTERS);
  const [includeTransport, setIncludeTransport] = useState(true);

  useEffect(() => {
    if (agentConfig && !localConfig) setLocalConfig(cloneConfig(agentConfig));
  }, [agentConfig]);

  const handleToggleAirport = async (
    code: string,
    group: "europeAirports" | "japanAirports",
    enabled: boolean,
  ) => {
    if (!localConfig) return;
    const updated = cloneConfig(localConfig);
    const ap = updated[group].find((a) => a.code === code);
    if (!ap) return;
    ap.enabled = enabled;
    setLocalConfig(updated);
    const token = loadToken();
    if (!token) return;
    const msg = `config: toggle ${code} ${enabled ? "aktivní" : "vypnuté"}`;
    try {
      await commitWithRetry(token, "main", serializeConfig(updated), msg);
      await commitWithRetry(token, "gh-pages", serializeConfig(updated), msg).catch(() => {});
    } catch {}
  };

  if (selectedRoute) {
    const relatedOffers = (latest ?? []).filter((o) => o.routeKey === selectedRoute);
    const routeStats = stats?.[selectedRoute] ?? null;
    return (
      <div className="mx-auto max-w-7xl p-6">
        <RouteDetailView
          routeKey={selectedRoute}
          stats={routeStats}
          relatedOffers={relatedOffers}
          onBack={() => setSelectedRoute(null)}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5 p-6">
      <header className="space-y-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Japan Flight Tracker</h1>
          <p className="text-sm text-muted-foreground">
            Evropa → Japonsko,{" "}
            {agentConfig
              ? `${agentConfig.travelWindow.from} – ${agentConfig.travelWindow.to}`
              : "září–prosinec 2026"}
            {agentConfig ? ` · doprava z ${agentConfig.homeLocation}` : ""}
          </p>
        </div>

        <nav className="flex gap-1 border-b">
          {(Object.keys(TAB_LABELS) as AppView[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={[
                "relative -mb-px rounded-t-md border-b-2 px-4 py-2 text-sm font-medium transition-colors",
                view === v
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              ].join(" ")}
            >
              {TAB_LABELS[v]}
            </button>
          ))}
        </nav>
      </header>

      {view === "offers" && (
        <>
          <HomePage
            latest={latest}
            stats={stats}
            agentConfig={agentConfig}
            loading={loading}
            error={error}
            onSelectRoute={setSelectedRoute}
            onFilteredOffersChange={setFilteredOffers}
            filters={filters}
            onFiltersChange={setFilters}
            includeTransport={includeTransport}
            onToggleTransport={setIncludeTransport}
          />
          <FlightMap
            routes={routes}
            latest={filteredOffers ?? latest}
            stats={stats}
            insights={null}
            agentConfig={agentConfig}
            onSelectRoute={setSelectedRoute}
            showInsights={false}
          />
        </>
      )}

      {view === "swimlanes" && (
        <SwimlanesView
          latest={latest}
          agentConfig={agentConfig}
          onSelectRoute={setSelectedRoute}
          priceMax={filters.priceMax}
          onPriceMaxChange={(v) => setFilters((f) => ({ ...f, priceMax: v }))}
          includeTransport={includeTransport}
          onToggleTransport={setIncludeTransport}
        />
      )}

      {view === "map" && (
        <FlightMap
          routes={routes}
          latest={latest}
          stats={stats}
          insights={insights}
          agentConfig={localConfig ?? agentConfig}
          onSelectRoute={setSelectedRoute}
          showMap={false}
          onToggleAirport={handleToggleAirport}
        />
      )}

      {view === "settings" && (
        <SettingsPage
          agentConfig={localConfig ?? agentConfig}
          loading={loading}
          error={error}
          onConfigChange={setLocalConfig}
        />
      )}
    </div>
  );
}
