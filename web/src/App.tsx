import { useState } from "react";
import { RouteDetailView } from "@/components/RouteDetailView";
import { useDataLoader } from "@/hooks/useDataLoader";
import { HomePage } from "@/pages/HomePage";

export default function App() {
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null);
  const { latest, stats } = useDataLoader();

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

  return <HomePage onSelectRoute={setSelectedRoute} />;
}
