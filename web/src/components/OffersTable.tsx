import { ArrowDownRight, ArrowUpRight, ExternalLink, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { effectivePrice, fmtDuration, getTransport } from "@/lib/transport";
import type { AgentConfig, LatestOffer } from "@/types/data";
import { airlineNames } from "@/lib/airlines";

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("cs-CZ", { day: "numeric", month: "numeric", year: "numeric" });
}

function routeLabel(o: LatestOffer): string {
  if (o.type === "openjaw" && o.returnOrigin) {
    return `${o.origin} → ${o.destination} / ${o.returnOrigin} → ${o.returnDestination ?? o.origin}`;
  }
  return `${o.origin} ⇄ ${o.destination}`;
}

function Flags({ o }: { o: LatestOffer }) {
  const { isNewLow, isBigDrop, priceDeltaEur, pctChange7d } = o.flags;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {isNewLow && (
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
          <Sparkles className="h-3 w-3" /> nové minimum
        </span>
      )}
      {isBigDrop && (
        <span className="inline-flex items-center gap-1 rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800">
          <ArrowDownRight className="h-3 w-3" /> velký pokles
        </span>
      )}
      {priceDeltaEur !== null && priceDeltaEur !== 0 && (
        <span
          className={cn(
            "inline-flex items-center gap-0.5 text-xs font-medium",
            priceDeltaEur < 0 ? "text-emerald-700" : "text-red-700",
          )}
        >
          {priceDeltaEur < 0 ? (
            <ArrowDownRight className="h-3 w-3" />
          ) : (
            <ArrowUpRight className="h-3 w-3" />
          )}
          {priceDeltaEur > 0 ? "+" : ""}
          {priceDeltaEur} €
        </span>
      )}
      {pctChange7d !== null && (
        <span className="text-xs text-muted-foreground">
          {pctChange7d > 0 ? "+" : ""}
          {pctChange7d} % / 7 d
        </span>
      )}
    </div>
  );
}

interface Props {
  offers: LatestOffer[];
  onSelectRoute?: (routeKey: string) => void;
  agentConfig?: AgentConfig | null;
  includeTransport?: boolean;
}

export function OffersTable({
  offers,
  onSelectRoute,
  agentConfig = null,
  includeTransport = false,
}: Props) {
  if (offers.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">
        Žádné nabídky neodpovídají filtrům.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Trasa</TableHead>
          <TableHead>Typ</TableHead>
          <TableHead className="text-right">
            {includeTransport ? "Cena vč. dopravy" : "Cena"}
          </TableHead>
          <TableHead>Odlet</TableHead>
          <TableHead>Návrat</TableHead>
          <TableHead className="text-right">Nocí</TableHead>
          <TableHead>Aerolinky</TableHead>
          <TableHead>Zdroj</TableHead>
          <TableHead>Signály</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {offers.map((o) => {
          const isOpenJaw = o.type === "openjaw" && o.returnDestination != null;
          const transport = includeTransport ? getTransport(o.origin, agentConfig) : null;
          const returnTransport =
            includeTransport && isOpenJaw
              ? getTransport(o.returnDestination!, agentConfig)
              : null;
          const displayed = effectivePrice(
            o.price,
            o.origin,
            agentConfig,
            includeTransport,
            o.returnDestination,
          );
          return (
            <TableRow key={`${o.routeKey}--${o.source}--${o.departDate ?? ""}--${o.price}`}>
              <TableCell className="font-medium">
                {onSelectRoute ? (
                  <button
                    onClick={() => onSelectRoute(o.routeKey)}
                    className="text-left hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    {routeLabel(o)}
                  </button>
                ) : (
                  routeLabel(o)
                )}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {o.type === "openjaw" ? "open-jaw" : "zpáteční"}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                <div className="font-semibold">{Math.round(displayed)} €</div>
                {isOpenJaw && transport && (
                  <div className="text-xs text-muted-foreground">
                    {returnTransport
                      ? `${(transport.durationMin / 60).toFixed(1)}h + ${(returnTransport.durationMin / 60).toFixed(1)}h`
                      : `${(transport.durationMin / 60).toFixed(1)}h cesta`}
                  </div>
                )}
                {!isOpenJaw && transport && (
                  <div className="text-xs text-muted-foreground">
                    {(transport.durationMin / 60).toFixed(1)}h cesta na letiště
                  </div>
                )}
                {includeTransport && !transport && (
                  <div className="text-xs text-muted-foreground">doprava neznámá</div>
                )}
              </TableCell>
              <TableCell className="tabular-nums">{fmtDate(o.departDate)}</TableCell>
              <TableCell className="tabular-nums">{fmtDate(o.returnDate)}</TableCell>
              <TableCell className="text-right tabular-nums">{o.nights ?? "—"}</TableCell>
              <TableCell>
                <div>{o.airlines.length > 0 ? airlineNames(o.airlines) : "—"}</div>
                {o.durationOutMin != null && (
                  <div className="text-xs text-muted-foreground">✈ {fmtDuration(o.durationOutMin)}</div>
                )}
                {o.scannedPrice != null && (
                  <div className="text-xs text-amber-600" title={`Scan: ${Math.round(o.scannedPrice)} €`}>
                    scan: {Math.round(o.scannedPrice)} €
                  </div>
                )}
              </TableCell>
              <TableCell className="text-muted-foreground">{o.source}</TableCell>
              <TableCell>
                <Flags o={o} />
              </TableCell>
              <TableCell>
                {o.dealUrl ? (
                  <Button asChild variant="ghost" size="sm">
                    <a href={o.dealUrl} target="_blank" rel="noreferrer">
                      Deal <ExternalLink className="h-3 w-3" />
                    </a>
                  </Button>
                ) : null}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
