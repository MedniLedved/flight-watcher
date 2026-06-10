import { TrendingDown, TrendingUp } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { RouteStats } from "@/types/data";

function Stat({ label, value, sub, highlight }: {
  label: string; value: string; sub?: string; highlight?: "green" | "red" | "neutral";
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={cn(
          "mt-1 text-xl font-bold tabular-nums",
          highlight === "green" && "text-emerald-700",
          highlight === "red" && "text-red-600",
        )}>{value}</p>
        {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
      </CardContent>
    </Card>
  );
}

export function StatsCards({ stats }: { stats: RouteStats }) {
  const trend = stats.trend30d;
  const trendEl = trend != null ? (
    <span className={cn("inline-flex items-center gap-0.5 font-semibold",
      trend < 0 ? "text-emerald-700" : "text-red-600")}>
      {trend < 0 ? <TrendingDown className="h-4 w-4" /> : <TrendingUp className="h-4 w-4" />}
      {trend > 0 ? "+" : ""}{trend} %
    </span>
  ) : "—";

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Stat
        label="Historické minimum"
        value={stats.allTimeMin != null ? `${stats.allTimeMin} €` : "—"}
        highlight="green"
      />
      <Stat
        label="Min / Max 90 dní"
        value={
          stats.min90d != null && stats.max90d != null
            ? `${stats.min90d} – ${stats.max90d} €`
            : "—"
        }
      />
      <Stat
        label="Průměr 90 dní"
        value={stats.avg90d != null ? `${Math.round(stats.avg90d)} €` : "—"}
      />
      <Card>
        <CardContent className="p-4">
          <p className="text-xs text-muted-foreground">Trend 30 dní</p>
          <p className="mt-1 text-xl">{trendEl}</p>
        </CardContent>
      </Card>
      <Stat
        label="vs. průměr 90 dní"
        value={stats.currentVsAvgPct != null
          ? `${stats.currentVsAvgPct > 0 ? "+" : ""}${stats.currentVsAvgPct} %`
          : "—"}
        highlight={
          stats.currentVsAvgPct == null ? "neutral"
          : stats.currentVsAvgPct < 0 ? "green" : "red"
        }
      />
      <Stat
        label="Největší pokles"
        value={stats.biggestDrop
          ? `${stats.biggestDrop.from} → ${stats.biggestDrop.to} €`
          : "—"}
        sub={stats.biggestDrop?.date}
        highlight={stats.biggestDrop ? "green" : undefined}
      />
    </div>
  );
}
