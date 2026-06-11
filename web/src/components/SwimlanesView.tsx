import { useMemo, useState } from "react";
import { ExternalLink, Train } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { priceColor } from "@/lib/colors";
import { effectivePrice, fmtDuration, getTransport } from "@/lib/transport";
import { cn } from "@/lib/utils";
import type { AgentConfig, LatestFile, LatestOffer } from "@/types/data";

const MONTH_NAMES = [
  "Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
  "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec",
];

const DAY_MS = 86_400_000;

function utcMs(iso: string): number {
  return Date.parse(iso + "T00:00:00Z");
}

function fmtDay(iso: string): string {
  const d = new Date(utcMs(iso));
  return `${d.getUTCDate()}. ${d.getUTCMonth() + 1}.`;
}

function laneLabel(o: LatestOffer): string {
  return o.type === "openjaw" && o.returnOrigin
    ? `${o.origin}→${o.destination} · ${o.returnOrigin}→${o.returnDestination ?? o.origin}`
    : `${o.origin}→${o.destination}`;
}

interface Props {
  latest: LatestFile | null;
  agentConfig: AgentConfig | null;
  onSelectRoute: (routeKey: string) => void;
}

/** Swimlanes: dny = sloupce (časová osa), nabídky = řádky seřazené podle
 *  nejbližšího odletu. Pozice barů v procentech časového rozsahu, takže
 *  layout je responzivní bez měření kontejneru. */
export function SwimlanesView({ latest, agentConfig, onSelectRoute }: Props) {
  const [selected, setSelected] = useState<LatestOffer | null>(null);
  const [includeTransport, setIncludeTransport] = useState(false);

  const offers = latest ?? [];
  const lanes = useMemo(
    () =>
      offers
        .filter((o) => o.departDate && o.returnDate)
        .sort((a, b) => a.departDate!.localeCompare(b.departDate!)),
    [offers],
  );
  const undated = offers.length - lanes.length;

  // Časový rozsah zarovnaný na hranice měsíců → čisté měsíční ticky.
  const { start, end, monthTicks, weekTicks, weekendBands } = useMemo(() => {
    if (lanes.length === 0) return { start: 0, end: 1, monthTicks: [], weekTicks: [], weekendBands: [] };
    const minDepart = new Date(Math.min(...lanes.map((o) => utcMs(o.departDate!))));
    const maxReturn = new Date(Math.max(...lanes.map((o) => utcMs(o.returnDate!))));
    const start = Date.UTC(minDepart.getUTCFullYear(), minDepart.getUTCMonth(), 1);
    const end = Date.UTC(maxReturn.getUTCFullYear(), maxReturn.getUTCMonth() + 1, 1);

    const monthTicks: { t: number; label: string }[] = [];
    for (let d = new Date(start); d.getTime() <= end; d.setUTCMonth(d.getUTCMonth() + 1)) {
      monthTicks.push({
        t: d.getTime(),
        label: `${MONTH_NAMES[d.getUTCMonth()]} ${d.getUTCFullYear()}`,
      });
    }

    const weekTicks: number[] = [];
    const firstMondayOffset = (8 - new Date(start).getUTCDay()) % 7;
    for (let t = start + firstMondayOffset * DAY_MS; t < end; t += 7 * DAY_MS) {
      weekTicks.push(t);
    }

    // Weekend bands: Saturday 00:00 UTC → Monday 00:00 UTC
    const weekendBands: { s: number; e: number }[] = [];
    const startDow = new Date(start).getUTCDay(); // 0=Sun,6=Sat
    const daysToFirstSat = ((6 - startDow) + 7) % 7;
    for (let t = start + daysToFirstSat * DAY_MS; t < end; t += 7 * DAY_MS) {
      weekendBands.push({ s: t, e: Math.min(t + 2 * DAY_MS, end) });
    }

    return { start, end, monthTicks, weekTicks, weekendBands };
  }, [lanes]);

  const pct = (t: number) => ((t - start) / (end - start)) * 100;

  const effPrices = useMemo(
    () => lanes.map((o) => effectivePrice(o.price, o.origin, agentConfig, includeTransport)),
    [lanes, agentConfig, includeTransport],
  );
  const minPrice = effPrices.length ? Math.min(...effPrices) : 0;
  const maxPrice = effPrices.length ? Math.max(...effPrices) : 0;

  if (lanes.length === 0) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">
          Žádné nabídky s konkrétním termínem odletu a návratu.
        </CardContent>
      </Card>
    );
  }

  const selectedTransport = selected ? getTransport(selected.origin, agentConfig) : null;
  const selectedEffPrice = selected
    ? effectivePrice(selected.price, selected.origin, agentConfig, includeTransport)
    : null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-4">
          <div>
            <CardTitle>Časová osa nabídek</CardTitle>
            <CardDescription>
              {lanes.length} nabídek seřazených podle nejbližšího odletu
              {undated > 0 ? ` · ${undated} bez termínu skryto` : ""}
              {" · "}Kliknutím na bar zobrazíš detail nabídky.
            </CardDescription>
          </div>
          <div className="flex items-center gap-4">
            {/* Legenda */}
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="tabular-nums">{Math.round(minPrice)} €</span>
              <div className="flex h-3 w-20 overflow-hidden rounded">
                {Array.from({ length: 16 }, (_, i) => (
                  <div
                    key={i}
                    className="flex-1"
                    style={{
                      background: priceColor(
                        minPrice + (i / 15) * (maxPrice - minPrice),
                        minPrice,
                        maxPrice,
                      ),
                    }}
                  />
                ))}
              </div>
              <span className="tabular-nums">{Math.round(maxPrice)} €</span>
            </div>
            <button
              onClick={() => setIncludeTransport((v) => !v)}
              className={cn(
                "flex h-9 items-center gap-2 rounded-md border px-3 text-sm font-medium transition-colors",
                includeTransport
                  ? "border-blue-400 bg-blue-50 text-blue-700 dark:border-blue-600 dark:bg-blue-950 dark:text-blue-300"
                  : "border-input bg-background text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Train className="h-4 w-4 shrink-0" />
              + doprava
            </button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex">
            {/* Levý sloupec s popisky tras */}
            <div className="w-44 shrink-0 pr-3">
              <div className="h-7" />
              {lanes.map((o) => (
                <div
                  key={o.routeKey}
                  className="flex h-9 items-center gap-1.5 text-xs font-medium"
                >
                  {o.flags.isNewLow && (
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"
                      title="Nové historické minimum"
                    />
                  )}
                  <span className="truncate" title={laneLabel(o)}>
                    {laneLabel(o)}
                  </span>
                </div>
              ))}
            </div>

            {/* Časová osa */}
            <div className="relative flex-1">
              {weekendBands.map(({ s, e }) => (
                <div
                  key={s}
                  className="absolute inset-y-0 bg-muted/50"
                  style={{ left: `${pct(s)}%`, width: `${pct(e) - pct(s)}%` }}
                />
              ))}
              {weekTicks.map((t) => (
                <div
                  key={t}
                  className="absolute inset-y-0 border-l border-border/40"
                  style={{ left: `${pct(t)}%` }}
                />
              ))}
              {monthTicks.map(({ t, label }) => (
                <div
                  key={t}
                  className="absolute inset-y-0 border-l border-border"
                  style={{ left: `${pct(t)}%` }}
                >
                  {pct(t) < 100 && (
                    <span className="absolute left-1 top-0 whitespace-nowrap text-[10px] font-medium text-muted-foreground">
                      {label}
                    </span>
                  )}
                </div>
              ))}

              <div className="h-7" />
              {lanes.map((o, i) => {
                const left = pct(utcMs(o.departDate!));
                const width = Math.max(pct(utcMs(o.returnDate!)) - left, 1.5);
                const eff = effPrices[i];
                const isSel = selected?.routeKey === o.routeKey;
                return (
                  <div key={o.routeKey} className="relative h-9">
                    <button
                      onClick={() => setSelected((prev) => (prev?.routeKey === o.routeKey ? null : o))}
                      className={cn(
                        "absolute inset-y-1 flex items-center overflow-hidden rounded px-2",
                        "text-[11px] font-semibold text-white transition-all hover:brightness-110",
                        isSel && "outline outline-2 outline-offset-1 outline-foreground",
                      )}
                      style={{
                        left: `${left}%`,
                        width: `${width}%`,
                        background: priceColor(eff, minPrice, maxPrice),
                      }}
                      title={`${laneLabel(o)} · ${fmtDay(o.departDate!)} – ${fmtDay(o.returnDate!)}`}
                    >
                      <span className="truncate tabular-nums">{Math.round(eff)} €</span>
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Detail vybrané nabídky */}
      {selected && (
        <div className="rounded-lg border bg-muted/40 p-4 text-sm">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p className="font-semibold">
              {laneLabel(selected)}
              {selected.flags.isNewLow && (
                <span className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 text-xs font-medium text-emerald-700">
                  nové minimum
                </span>
              )}
            </p>
            <div className="flex gap-2">
              {selected.dealUrl && (
                <Button asChild size="sm" variant="outline">
                  <a href={selected.dealUrl} target="_blank" rel="noreferrer">
                    <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                    Otevřít deal
                  </a>
                </Button>
              )}
              <Button size="sm" onClick={() => onSelectRoute(selected.routeKey)}>
                Detail trasy
              </Button>
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-1 sm:grid-cols-4">
            <dt className="text-muted-foreground">Odlet</dt>
            <dd className="tabular-nums">{fmtDay(selected.departDate!)}</dd>
            <dt className="text-muted-foreground">Návrat</dt>
            <dd className="tabular-nums">{fmtDay(selected.returnDate!)}</dd>
            <dt className="text-muted-foreground">Délka pobytu</dt>
            <dd>{selected.nights != null ? `${selected.nights} nocí` : "—"}</dd>
            <dt className="text-muted-foreground">
              Cena{includeTransport ? " vč. dopravy" : ""}
            </dt>
            <dd className="font-bold tabular-nums">
              {selectedEffPrice != null ? Math.round(selectedEffPrice) : "—"} €
            </dd>
            <dt className="text-muted-foreground">Aerolinky</dt>
            <dd>{selected.airlines.length ? selected.airlines.join(", ") : "—"}</dd>
            <dt className="text-muted-foreground">Zdroj</dt>
            <dd>{selected.source}</dd>
            {includeTransport && selectedTransport && (
              <>
                <dt className="text-muted-foreground">Doprava na letiště</dt>
                <dd>
                  2× {selectedTransport.costEur} € · {fmtDuration(selectedTransport.durationMin)} (
                  {selectedTransport.mode})
                </dd>
              </>
            )}
          </dl>
        </div>
      )}
    </div>
  );
}
