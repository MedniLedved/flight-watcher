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
import type { FlightSegment, LatestOffer, RouteStats } from "@/types/data";
import { airlineNames, airlineName } from "@/lib/airlines";
import { fmtDuration } from "@/lib/transport";

function routeLabel(routeKey: string): string {
  const parts = routeKey.split("-");
  const kind = parts.at(-1);
  if (kind === "roundtrip") return `${parts[0]} ⇄ ${parts[1]}`;
  if (kind === "openjaw" && parts.length === 4)
    return `${parts[0]} → ${parts[1]} / ${parts[2]} → ${parts[0]}`;
  return routeKey;
}

function InlineSegments({ segments }: { segments: FlightSegment[] }) {
  return (
    <div className="mt-0.5 space-y-px text-xs text-gray-500">
      {segments.map((s, i) => (
        <div key={i} className="flex items-center gap-1">
          {s.layoverMin != null && i > 0 && (
            <span className="text-amber-500">⏱{fmtDuration(s.layoverMin)}</span>
          )}
          <span className="font-mono">{s.from}→{s.to}</span>
          {s.airline && <span>· {airlineName(s.airline)}</span>}
          {s.durationMin != null && <span>({fmtDuration(s.durationMin)})</span>}
        </div>
      ))}
    </div>
  );
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
                      {o.scannedPrice != null && (
                        <div className="text-xs font-normal text-amber-600" title="Původní cena ze scanu">
                          scan: {Math.round(o.scannedPrice)} €
                        </div>
                      )}
                    </TableCell>
                    <TableCell>{o.nights ?? "—"}</TableCell>
                    <TableCell>
                      <div>{o.airlines.length ? airlineNames(o.airlines) : "—"}</div>
                      {o.durationOutMin != null && (
                        <div className="text-xs text-muted-foreground">✈ {fmtDuration(o.durationOutMin)}</div>
                      )}
                      {(o.segments?.out?.length ?? 0) > 0 && (
                        <InlineSegments segments={o.segments!.out} />
                      )}
                    </TableCell>
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
