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
import { effectivePrice } from "@/lib/transport";
import type { AgentConfig, LatestFile, StatsFile } from "@/types/data";

interface Props {
  latest: LatestFile | null;
  stats: StatsFile | null;
  agentConfig: AgentConfig | null;
  loading: boolean;
  error: string | null;
  onSelectRoute: (routeKey: string) => void;
}

function SummaryBar({
  stats,
  offers,
  agentConfig,
  includeTransport,
}: {
  stats: StatsFile;
  offers: LatestFile;
  agentConfig: AgentConfig | null;
  includeTransport: boolean;
}) {
  const routes = Object.keys(stats);
  const trending = routes.filter((k) => (stats[k].trend30d ?? 0) < -5).length;

  const bestPrice = useMemo(() => {
    if (includeTransport && offers.length > 0) {
      return Math.min(
        ...offers.map((o) => effectivePrice(o.price, o.origin, agentConfig, true)),
      );
    }
    const allMins = routes
      .map((k) => stats[k].allTimeMin)
      .filter((v): v is number => v != null);
    return allMins.length ? Math.min(...allMins) : null;
  }, [includeTransport, offers, agentConfig, routes, stats]);

  return (
    <div className="flex flex-wrap gap-6 rounded-lg border bg-muted/40 px-5 py-3 text-sm">
      <span>
        <span className="font-semibold text-emerald-700">
          {bestPrice != null ? `${Math.round(bestPrice)} €` : "—"}
        </span>{" "}
        {includeTransport ? "nejnižší cena vč. dopravy" : "nejnižší nalezená cena"}
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

export function HomePage({ latest, stats, agentConfig, loading, error, onSelectRoute }: Props) {
  const [filters, setFilters] = useState<OfferFilters>(EMPTY_FILTERS);
  const [includeTransport, setIncludeTransport] = useState(false);

  const offers = useMemo(() => latest ?? [], [latest]);
  const visible = useMemo(
    () => applyFilters(offers, filters, agentConfig, includeTransport),
    [offers, filters, agentConfig, includeTransport],
  );

  if (loading) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">Načítám data…</p>
    );
  }

  if (error) {
    return (
      <Card className="border-destructive">
        <CardContent className="p-4 text-sm text-destructive">
          Nepodařilo se načíst data: {error}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {stats && (
        <SummaryBar
          stats={stats}
          offers={offers}
          agentConfig={agentConfig}
          includeTransport={includeTransport}
        />
      )}
      <FilterBar
        offers={offers}
        filters={filters}
        onChange={setFilters}
        includeTransport={includeTransport}
        onToggleTransport={setIncludeTransport}
      />
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
          <OffersTable
            offers={visible}
            onSelectRoute={onSelectRoute}
            agentConfig={agentConfig}
            includeTransport={includeTransport}
          />
        </CardContent>
      </Card>
    </div>
  );
}
