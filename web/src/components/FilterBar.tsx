import { Train } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { effectivePrice } from "@/lib/transport";
import type { AgentConfig, LatestOffer } from "@/types/data";

/** Hodnota „bez filtru" pro selecty (Radix nepovoluje prázdný string). */
export const ALL = "__all__";

export interface OfferFilters {
  origin: string; // IATA kód nebo ALL
  destination: string;
  priceMin: string; // text z inputu; prázdné = bez omezení
  priceMax: string;
  nightsMin: string;
  nightsMax: string;
}

export const EMPTY_FILTERS: OfferFilters = {
  origin: ALL,
  destination: ALL,
  priceMin: "",
  priceMax: "900",
  nightsMin: "",
  nightsMax: "",
};

function parseNum(raw: string): number | null {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** Klientské filtrování v paměti. Cenový filtr se aplikuje na efektivní cenu
 *  (= cena letu + 2× doprava), pokud je includeTransport zapnuto. */
export function applyFilters(
  offers: LatestOffer[],
  f: OfferFilters,
  agentConfig: AgentConfig | null = null,
  includeTransport = false,
): LatestOffer[] {
  const priceMin = parseNum(f.priceMin);
  const priceMax = parseNum(f.priceMax);
  const nightsMin = parseNum(f.nightsMin);
  const nightsMax = parseNum(f.nightsMax);
  return offers.filter((o) => {
    if (f.origin !== ALL && o.origin !== f.origin) return false;
    if (f.destination !== ALL && o.destination !== f.destination) return false;
    const displayedPrice = effectivePrice(o.price, o.origin, agentConfig, includeTransport, o.returnDestination);
    if (priceMin !== null && displayedPrice < priceMin) return false;
    if (priceMax !== null && displayedPrice > priceMax) return false;
    if (nightsMin !== null && (o.nights === null || o.nights < nightsMin)) return false;
    if (nightsMax !== null && (o.nights === null || o.nights > nightsMax)) return false;
    return true;
  });
}

interface FilterBarProps {
  offers: LatestOffer[];
  filters: OfferFilters;
  onChange: (filters: OfferFilters) => void;
  includeTransport: boolean;
  onToggleTransport: (v: boolean) => void;
}

export function FilterBar({
  offers,
  filters,
  onChange,
  includeTransport,
  onToggleTransport,
}: FilterBarProps) {
  const origins = [...new Set(offers.map((o) => o.origin))].sort();
  const destinations = [...new Set(offers.map((o) => o.destination))].sort();
  const set = (patch: Partial<OfferFilters>) => onChange({ ...filters, ...patch });

  return (
    <Card>
      <CardContent className="flex flex-wrap items-end gap-4 p-4">
        <div className="w-40 space-y-1">
          <label className="text-xs font-medium text-muted-foreground">
            Odletové letiště
          </label>
          <Select value={filters.origin} onValueChange={(v) => set({ origin: v })}>
            <SelectTrigger>
              <SelectValue placeholder="Vše" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Všechna</SelectItem>
              {origins.map((code) => (
                <SelectItem key={code} value={code}>{code}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="w-40 space-y-1">
          <label className="text-xs font-medium text-muted-foreground">
            Destinace
          </label>
          <Select value={filters.destination} onValueChange={(v) => set({ destination: v })}>
            <SelectTrigger>
              <SelectValue placeholder="Vše" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Všechny</SelectItem>
              {destinations.map((code) => (
                <SelectItem key={code} value={code}>{code}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">
            Cena (EUR){includeTransport ? " vč. dopravy" : ""}
          </label>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-0.5">
              <Button
                variant="outline"
                size="sm"
                className="h-9 w-7 px-0 text-base"
                onClick={() => {
                  const v = parseNum(filters.priceMin);
                  if (v != null) set({ priceMin: v <= 50 ? "" : String(v - 50) });
                }}
              >−</Button>
              <Input
                type="number"
                inputMode="numeric"
                placeholder="od"
                className="w-20"
                value={filters.priceMin}
                onChange={(e) => set({ priceMin: e.target.value })}
              />
              <Button
                variant="outline"
                size="sm"
                className="h-9 w-7 px-0 text-base"
                onClick={() => {
                  const v = parseNum(filters.priceMin) ?? 0;
                  set({ priceMin: String(v + 50) });
                }}
              >+</Button>
            </div>
            <span className="text-muted-foreground">–</span>
            <div className="flex items-center gap-0.5">
              <Button
                variant="outline"
                size="sm"
                className="h-9 w-7 px-0 text-base"
                onClick={() => {
                  const v = parseNum(filters.priceMax);
                  if (v != null) set({ priceMax: v <= 50 ? "0" : String(v - 50) });
                }}
              >−</Button>
              <Input
                type="number"
                inputMode="numeric"
                placeholder="do"
                className="w-20"
                value={filters.priceMax}
                onChange={(e) => set({ priceMax: e.target.value })}
              />
              <Button
                variant="outline"
                size="sm"
                className="h-9 w-7 px-0 text-base"
                onClick={() => {
                  const v = parseNum(filters.priceMax);
                  if (v != null) set({ priceMax: String(v + 50) });
                }}
              >+</Button>
            </div>
          </div>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">
            Délka pobytu (nocí)
          </label>
          <div className="flex items-center gap-2">
            <Input
              type="number"
              inputMode="numeric"
              placeholder="min"
              className="w-20"
              value={filters.nightsMin}
              onChange={(e) => set({ nightsMin: e.target.value })}
            />
            <span className="text-muted-foreground">–</span>
            <Input
              type="number"
              inputMode="numeric"
              placeholder="max"
              className="w-20"
              value={filters.nightsMax}
              onChange={(e) => set({ nightsMax: e.target.value })}
            />
          </div>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">
            Celkové náklady
          </label>
          <button
            onClick={() => onToggleTransport(!includeTransport)}
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

        <Button variant="outline" onClick={() => onChange(EMPTY_FILTERS)}>
          Zrušit filtry
        </Button>
      </CardContent>
    </Card>
  );
}
