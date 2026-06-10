import { useState } from "react";
import { FlightMap } from "@/components/FlightMap";
import { RouteDetailView } from "@/components/RouteDetailView";
import { useDataLoader } from "@/hooks/useDataLoader";
import { HomePage } from "@/pages/HomePage";

type AppView = "offers" | "map";

const TAB_LABELS: Record<AppView, string> = {
  offers: "Nabídky",
  map: "Mapa",
};

export default function App() {
  const [view, setView] = useState<AppView>("offers");
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null);
  const { latest, stats, agentConfig, routes, loading, error } = useDataLoader();

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
        <HomePage
          latest={latest}
          stats={stats}
          loading={loading}
          error={error}
          onSelectRoute={setSelectedRoute}
        />
      )}

      {view === "map" && (
        <FlightMap
          routes={routes}
          latest={latest}
          stats={stats}
          onSelectRoute={setSelectedRoute}
        />
      )}
    </div>
  );
}
