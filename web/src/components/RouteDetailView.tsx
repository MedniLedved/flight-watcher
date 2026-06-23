import { Fragment, useMemo } from "react";
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
import type {
  AlternativeRecord, FlightSegment, LatestOffer, RouteStats,
} from "@/types/data";
import { airlineNames, airlineName } from "@/lib/airlines";
import { fmtDuration } from "@/lib/transport";

interface AltOption {
  airlines: string[];
  stopsOut: number | null;
  stopsIn: number | null;
  lastPrice: number;
  minPrice: number;
  count: number;
  firstDate: string;
  lastDate: string;
}

/** Seskupí historické alternativy podle „varianty" (aerolinky + přestupy) a
 *  spočítá poslední/min cenu, počet pozorování a období. */
function groupAlternatives(alts: AlternativeRecord[]): AltOption[] {
  const map = new Map<string, AltOption>();
  for (const a of [...alts].sort((x, y) => x.date.localeCompare(y.date))) {
    const key = `${[...a.airlines].sort().join(",")}|${a.stopsOut}|${a.stopsIn}`;
    const ex = map.get(key);
    if (!ex) {
      map.set(key, {
        airlines: a.airlines, stopsOut: a.stopsOut, stopsIn: a.stopsIn,
        lastPrice: a.price, minPrice: a.price, count: 1,
        firstDate: a.date, lastDate: a.date,
      });
    } else {
      ex.lastPrice = a.price; // řazeno dle data vzestupně → poslední vyhrává
      ex.lastDate = a.date;
      ex.minPrice = Math.min(ex.minPrice, a.price);
      ex.count += 1;
    }
  }
  return [...map.values()].sort((a, b) => a.lastPrice - b.lastPrice);
}

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

/** Popisek přestupů: "přímý" když 0/0, jinak "1×" nebo "0× / 1×" (tam/zpět). */
function stopsLabel(
  stopsOut?: number | null, stopsIn?: number | null,
): string | null {
  if (stopsOut == null && stopsIn == null) return null;
  const one = (n?: number | null) =>
    n == null ? "?" : n === 0 ? "přímý" : `${n}×`;
  if (stopsOut === stopsIn) return one(stopsOut);
  return `${one(stopsOut)} / ${one(stopsIn)}`;
}

/** Přímý let (0 přestupů tam i zpět) = zelená, jinak jantarová. */
function isDirect(stopsOut?: number | null, stopsIn?: number | null): boolean {
  return stopsOut === 0 && stopsIn === 0;
}

interface Props {
  routeKey: string;
  stats: RouteStats | null;
  relatedOffers: LatestOffer[];
  onBack: () => void;
}

export function RouteDetailView({ routeKey, stats, relatedOffers, onBack }: Props) {
  const { history, calendar, alternatives, loading, error } = useRouteDetail(routeKey);
  const altOptions = useMemo(() => groupAlternatives(alternatives), [alternatives]);

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
                {relatedOffers.map((o) => {
                  const stops = stopsLabel(o.stopsOut, o.stopsIn);
                  return (
                  <Fragment key={o.routeKey + o.source + (o.departDate ?? "") + (o.returnDate ?? "") + o.price}>
                  <TableRow className={(o.flags?.staleDays ?? 0) > 0 ? "opacity-60" : undefined}>
                    <TableCell>{fmtDate(o.departDate)}</TableCell>
                    <TableCell>{fmtDate(o.returnDate)}</TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {Math.round(o.price)} €
                      {(o.flags?.staleDays ?? 0) > 0 && (
                        <div className="text-xs font-normal text-amber-600"
                             title="Poslední známá cena, ne živá nabídka">
                          🕓 archiv {o.flags!.staleDays} d
                        </div>
                      )}
                      {o.scannedPrice != null && (
                        <div className="text-xs font-normal text-amber-600" title="Původní cena ze scanu">
                          scan: {Math.round(o.scannedPrice)} €
                        </div>
                      )}
                    </TableCell>
                    <TableCell>{o.nights ?? "—"}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span>{o.airlines.length ? airlineNames(o.airlines) : "—"}</span>
                        {stops && (
                          <span className={isDirect(o.stopsOut, o.stopsIn)
                            ? "text-xs text-emerald-600" : "text-xs text-amber-600"}>
                            {stops}
                          </span>
                        )}
                      </div>
                      {o.durationOutMin != null && (
                        <div className="text-xs text-muted-foreground">✈ {fmtDuration(o.durationOutMin)}</div>
                      )}
                      {(o.segments?.out?.length ?? 0) > 0 && (
                        <InlineSegments segments={o.segments!.out} />
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{o.source}</TableCell>
                  </TableRow>
                  {(o.alternatives?.length ?? 0) > 0 && (
                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                      <TableCell colSpan={6} className="py-2">
                        <div className="mb-1 text-xs font-medium text-muted-foreground">
                          Další nabídky na stejný termín (dražší, ale jiná aerolinka / přímý let):
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {o.alternatives!.map((a) => {
                            const delta = Math.round(a.price - o.price);
                            const aStops = stopsLabel(a.stopsOut, a.stopsIn);
                            const altKey = `${[...a.airlines].sort().join(",")}|${a.stopsOut}|${a.stopsIn}|${a.price}`;
                            return (
                              <div key={altKey} className="flex items-center gap-2 rounded border bg-background px-2 py-1 text-xs">
                                <span className="font-medium">
                                  {a.airlines.length ? airlineNames(a.airlines) : "—"}
                                </span>
                                {aStops && (
                                  <span className={isDirect(a.stopsOut, a.stopsIn)
                                    ? "text-emerald-600" : "text-amber-600"}>
                                    {aStops}
                                  </span>
                                )}
                                <span className="font-semibold tabular-nums">{Math.round(a.price)} €</span>
                                {delta > 0 && (
                                  <span className="text-muted-foreground">(+{delta} €)</span>
                                )}
                                {a.dealUrl && (
                                  <a href={a.dealUrl} target="_blank" rel="noreferrer"
                                     className="text-blue-600 underline">odkaz</a>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                  </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Historie dražších-ale-lepších variant (přímý let / prémiová aerolinka) */}
      {altOptions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Dražší varianty v čase (přímý let / lepší aerolinka)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-2 text-xs text-muted-foreground">
              Tyto nabídky jsou nad nejlevnější cenou, ale můžou být přímé nebo
              s lepší aerolinkou. Nepočítají se do cenových statistik výše.
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Aerolinka</TableHead>
                  <TableHead>Přestupy</TableHead>
                  <TableHead className="text-right">Poslední cena</TableHead>
                  <TableHead className="text-right">Min</TableHead>
                  <TableHead className="text-right">Pozorování</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {altOptions.map((opt, i) => {
                  const stops = stopsLabel(opt.stopsOut, opt.stopsIn);
                  return (
                    <TableRow key={i}>
                      <TableCell>
                        {opt.airlines.length ? airlineNames(opt.airlines) : "—"}
                      </TableCell>
                      <TableCell>
                        {stops ? (
                          <span className={isDirect(opt.stopsOut, opt.stopsIn)
                            ? "text-emerald-600" : "text-amber-600"}>{stops}</span>
                        ) : "—"}
                      </TableCell>
                      <TableCell className="text-right font-semibold tabular-nums">
                        {Math.round(opt.lastPrice)} €
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {Math.round(opt.minPrice)} €
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {opt.count}×
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
