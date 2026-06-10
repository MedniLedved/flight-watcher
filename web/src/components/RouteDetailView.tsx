import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { CalendarHeatmap } from "./CalendarHeatmap";
import { PriceHistoryChart } from "./PriceHistoryChart";
import { StatsCards } from "./StatsCards";
import { useRouteDetail } from "@/hooks/useRouteDetail";
import type { LatestOffer, RouteStats } from "@/types/data";

function routeLabel(routeKey: string): string {
  const parts = routeKey.split("-");
  const kind = parts.at(-1);
  if (kind === "roundtrip") return `${parts[0]} ⇄ ${parts[1]}`;
  if (kind === "openjaw" && parts.length === 4)
    return `${parts[0]} → ${parts[1]} / ${parts[2]} → ${parts[0]}`;
  return routeKey;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("cs-CZ", {
    day: "numeric", month: "numeric", year: "numeric",
  });
}

interface Props {
  routeKey: string;
  stats: RouteStats | null;
  relatedOffers: LatestOffer[];
  onBack: () => void;
}

export function RouteDetailView({ routeKey, stats, relatedOffers, onBack }: Props) {
  const { history, calendar, loading, error } = useRouteDetail(routeKey);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" /> Zpět
        </Button>
        <h2 className="text-xl font-bold">{routeLabel(routeKey)}</h2>
        <span className="text-sm text-muted-foreground">{routeKey}</span>
      </div>

      {/* Statistiky */}
      {stats && <StatsCards stats={stats} />}

      {/* Graf vývoje ceny */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Vývoj ceny (denní minimum)</CardTitle>
        </CardHeader>
        <CardContent>
          {loading && (
            <p className="py-8 text-center text-sm text-muted-foreground">Načítám historii…</p>
          )}
          {error && (
            <p className="py-4 text-sm text-destructive">
              Nelze načíst historii: {error}
            </p>
          )}
          {history && history.length > 0 && (
            <PriceHistoryChart history={history} stats={stats} />
          )}
          {history && history.length === 0 && (
            <p className="py-4 text-sm text-muted-foreground">Žádná data v historii.</p>
          )}
        </CardContent>
      </Card>

      {/* Kalendář termínů — heatmapa */}
      {calendar && calendar.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Kalendář dostupných termínů</CardTitle>
          </CardHeader>
          <CardContent>
            <CalendarHeatmap calendar={calendar} />
          </CardContent>
        </Card>
      )}

      {/* Aktuální nabídky na trase */}
      {relatedOffers.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Aktuální nabídky</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Odlet</TableHead>
                  <TableHead>Návrat</TableHead>
                  <TableHead className="text-right">Cena</TableHead>
                  <TableHead>Nocí</TableHead>
                  <TableHead>Aerolinky</TableHead>
                  <TableHead>Zdroj</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {relatedOffers.map((o) => (
                  <TableRow key={o.routeKey + o.source}>
                    <TableCell>{fmtDate(o.departDate)}</TableCell>
                    <TableCell>{fmtDate(o.returnDate)}</TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {Math.round(o.price)} €
                    </TableCell>
                    <TableCell>{o.nights ?? "—"}</TableCell>
                    <TableCell>{o.airlines.join(", ") || "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{o.source}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
