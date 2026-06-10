import { useMemo, useState } from "react";

import type { CalendarDay } from "@/types/data";

const DOW = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"];
const MONTH_NAMES = [
  "Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
  "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec",
];

function priceColor(price: number, min: number, max: number): string {
  const t = max === min ? 0 : (price - min) / (max - min);
  // green #10b981 → yellow #f59e0b → red #ef4444
  if (t < 0.5) {
    const u = t * 2;
    return `rgb(${Math.round(16 + 229 * u)},${Math.round(185 - 27 * u)},${Math.round(129 - 118 * u)})`;
  }
  const u = (t - 0.5) * 2;
  return `rgb(${Math.round(245 - 6 * u)},${Math.round(158 - 90 * u)},${Math.round(11 + 57 * u)})`;
}

function monthKey(iso: string): string {
  return iso.slice(0, 7);
}

function dayOfWeekIso(iso: string): number {
  // 0=Mon … 6=Sun
  return (new Date(iso + "T12:00:00Z").getUTCDay() + 6) % 7;
}

function daysInMonth(year: number, month1: number): number {
  return new Date(year, month1, 0).getDate();
}

function pill(active: boolean): string {
  return [
    "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
    active
      ? "border-foreground bg-foreground text-background"
      : "border-border text-muted-foreground hover:border-foreground hover:text-foreground",
  ].join(" ");
}

interface Props {
  calendar: CalendarDay[];
}

export function CalendarHeatmap({ calendar }: Props) {
  const [selected, setSelected] = useState<CalendarDay | null>(null);
  const [activeMonth, setActiveMonth] = useState<string | null>(null);

  const byDate = useMemo(() => {
    const m = new Map<string, CalendarDay>();
    for (const d of calendar) m.set(d.departDate, d);
    return m;
  }, [calendar]);

  const months = useMemo(
    () => [...new Set(calendar.map((d) => monthKey(d.departDate)))].sort(),
    [calendar],
  );

  const minPrice = useMemo(() => Math.min(...calendar.map((d) => d.price)), [calendar]);
  const maxPrice = useMemo(() => Math.max(...calendar.map((d) => d.price)), [calendar]);

  const shown = activeMonth ? [activeMonth] : months;

  function toggleDay(d: CalendarDay) {
    setSelected((prev) => (prev?.departDate === d.departDate ? null : d));
  }

  return (
    <div className="space-y-4">
      {/* Month filter pills */}
      <div className="flex flex-wrap gap-2">
        <button onClick={() => setActiveMonth(null)} className={pill(activeMonth === null)}>
          Vše
        </button>
        {months.map((m) => {
          const [y, mo] = m.split("-");
          return (
            <button
              key={m}
              onClick={() => setActiveMonth(m === activeMonth ? null : m)}
              className={pill(activeMonth === m)}
            >
              {MONTH_NAMES[+mo - 1]} {y}
            </button>
          );
        })}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span>{minPrice} € (min)</span>
        <div className="flex h-3 w-24 overflow-hidden rounded">
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
        <span>{maxPrice} € (max)</span>
      </div>

      {/* Calendar grids */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {shown.map((mk) => {
          const [yStr, moStr] = mk.split("-");
          const y = +yStr;
          const mo = +moStr;
          const days = daysInMonth(y, mo);
          const startDow = dayOfWeekIso(`${mk}-01`);

          return (
            <div key={mk} className="rounded-lg border p-4">
              <p className="mb-3 text-sm font-semibold">
                {MONTH_NAMES[mo - 1]} {y}
              </p>
              <div className="grid grid-cols-7 gap-1">
                {DOW.map((d) => (
                  <div
                    key={d}
                    className="py-1 text-center text-[10px] font-medium text-muted-foreground"
                  >
                    {d}
                  </div>
                ))}
                {Array.from({ length: startDow }, (_, i) => (
                  <div key={`pre${i}`} />
                ))}
                {Array.from({ length: days }, (_, i) => {
                  const day = i + 1;
                  const iso = `${mk}-${String(day).padStart(2, "0")}`;
                  const entry = byDate.get(iso);
                  const isSel = selected?.departDate === iso;

                  return (
                    <button
                      key={iso}
                      disabled={!entry}
                      onClick={() => entry && toggleDay(entry)}
                      className={[
                        "flex min-h-[40px] flex-col items-center justify-center rounded p-1",
                        "text-[10px] leading-tight transition-all",
                        entry ? "cursor-pointer" : "cursor-default opacity-20",
                        isSel
                          ? "outline outline-2 outline-offset-1 outline-foreground"
                          : "",
                      ].join(" ")}
                      style={
                        entry
                          ? {
                              background: priceColor(entry.price, minPrice, maxPrice),
                              color: "#fff",
                            }
                          : undefined
                      }
                    >
                      <span className="font-semibold">{day}</span>
                      {entry && (
                        <span className="tabular-nums">{Math.round(entry.price)}€</span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* Selected day detail */}
      {selected && (
        <div className="rounded-lg border bg-muted/40 p-4 text-sm">
          <p className="mb-2 font-semibold">Vybraný termín</p>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-1 sm:grid-cols-4">
            <dt className="text-muted-foreground">Odlet</dt>
            <dd className="tabular-nums">{selected.departDate}</dd>
            <dt className="text-muted-foreground">Návrat</dt>
            <dd className="tabular-nums">{selected.returnDate ?? "—"}</dd>
            <dt className="text-muted-foreground">Cena</dt>
            <dd className="font-bold tabular-nums">{Math.round(selected.price)} €</dd>
            <dt className="text-muted-foreground">Zdroj</dt>
            <dd>{selected.source}</dd>
            <dt className="text-muted-foreground">Pozorováno</dt>
            <dd>{selected.observedDate}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}
