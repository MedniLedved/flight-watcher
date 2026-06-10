import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HistoryFile, RouteStats } from "@/types/data";

interface ChartPoint {
  date: string;
  min: number;
}

function toDailyMin(records: HistoryFile): ChartPoint[] {
  const acc: Record<string, number> = {};
  for (const r of records) {
    if (!r.date || r.price == null) continue;
    acc[r.date] = acc[r.date] == null ? r.price : Math.min(acc[r.date], r.price);
  }
  return Object.entries(acc)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, min]) => ({ date: date.slice(5), min })); // "MM-DD"
}

interface Props {
  history: HistoryFile;
  stats?: RouteStats | null;
}

export function PriceHistoryChart({ history, stats }: Props) {
  const data = toDailyMin(history);
  if (data.length === 0) return null;

  const prices = data.map((d) => d.min);
  const domainMin = Math.floor(Math.min(...prices) / 50) * 50 - 50;
  const domainMax = Math.ceil(Math.max(...prices) / 50) * 50 + 50;

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11 }}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          domain={[domainMin, domainMax]}
          tickFormatter={(v: number) => `${v} €`}
          tick={{ fontSize: 11 }}
          tickLine={false}
          width={60}
        />
        <Tooltip
          formatter={(value) => [`${value} €`, "Min. cena"]}
          labelFormatter={(label) => `Pozorováno: ${String(label)}`}
        />
        {stats?.allTimeMin != null && (
          <ReferenceLine
            y={stats.allTimeMin}
            stroke="#16a34a"
            strokeDasharray="4 2"
            label={{ value: `min ${stats.allTimeMin} €`, position: "insideTopRight", fontSize: 10, fill: "#16a34a" }}
          />
        )}
        <Line
          type="monotone"
          dataKey="min"
          stroke="#2563eb"
          strokeWidth={2}
          dot={data.length <= 15}
          activeDot={{ r: 5 }}
          name="Min. cena (EUR)"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
