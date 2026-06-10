import { useMemo, useState } from "react";

import {
  applyFilters,
  EMPTY_FILTERS,
  FilterBar,
  type OfferFilters,
} from "@/components/FilterBar";
import { OffersTable } from "@/components/OffersTable";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useDataLoader } from "@/hooks/useDataLoader";
import type { StatsFile } from "@/types/data";

interface Props {
  onSelectRoute: (routeKey: string) => void;
}

function SummaryBar({ stats }: { stats: StatsFile }) {
  const routes = Object.keys(stats);
  const allMins = routes
    .map((k) => stats[k].allTimeMin)
    .filter((v): v is number => v != null);
  const bestMin = allMins.length ? Math.min(...allMins) : null;
  const trending = routes
    .filter((k) => (stats[k].trend30d ?? 0) < -5)
    .length;
  return (
    <div className="flex flex-wrap gap-6 rounded-lg border bg-muted/40 px-5 py-3 text-sm">
      <span>
        <span className="font-semibold text-emerald-700">{bestMin != null ? `${bestMin} €` : "—"}</span>
        {" "}nejnižší nalezená cena
      </span>
      <span>
        <span className="font-semibold">{routes.length}</span> sledovaných tras
      </span>
      <span>
        <span className="font-semibold text-emerald-700">{trending}</span> tras v poklesu &gt; 5 % / 30 dní
      </span>
    </div>
  );
}

export function HomePage({ onSelectRoute }: Props) {
  const { latest, stats, agentConfig, loading, error } = useDataLoader();
  const [filters, setFilters] = useState<OfferFilters>(EMPTY_FILTERS);

  const offers = useMemo(() => latest ?? [], [latest]);
  const visible = useMemo(() => applyFilters(offers, filters), [offers, filters]);

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight">Japan Flight Tracker</h1>
        <p className="text-sm text-muted-foreground">
          Evropa → Japonsko,{" "}
          {agentConfig
            ? `${agentConfig.travelWindow.from} – ${agentConfig.travelWindow.to}`
            : "září–prosinec 2026"}
          {agentConfig ? ` · doprava z ${agentConfig.homeLocation}` : ""}
        </p>
      </header>

      {loading && (
        <p className="py-10 text-center text-sm text-muted-foreground">Načítám data…</p>
      )}
      {error && (
        <Card className="border-destructive">
          <CardContent className="p-4 text-sm text-destructive">
            Nepodařilo se načíst data: {error}
          </CardContent>
        </Card>
      )}

      {!loading && !error && (
        <>
          {stats && <SummaryBar stats={stats} />}
          <FilterBar offers={offers} filters={filters} onChange={setFilters} />
          <Card>
            <CardHeader>
              <CardTitle>Aktuální nejlepší nabídky</CardTitle>
              <CardDescription>
                {visible.length} z {offers.length} nabídek
                {offers[0] ? ` · pozorováno ${offers[0].observedDate}` : ""}
                {" · "}Kliknutím na trasu zobrazíš detail a graf vývoje ceny.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <OffersTable offers={visible} onSelectRoute={onSelectRoute} />
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
