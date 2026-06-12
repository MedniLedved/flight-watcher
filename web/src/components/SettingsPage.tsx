import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Download, GripVertical, Play, Plus, Save, Trash2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  cloneConfig,
  emptyEuropeAirport,
  emptyJapanAirport,
  serializeConfig,
  validateAgentConfig,
} from "@/lib/agentConfig";
import {
  AGENT_CONFIG_PATH,
  commitWithRetry,
  loadToken,
  saveToken,
  triggerScan,
} from "@/lib/github";
import type { AgentAirport, AgentConfig } from "@/types/data";

interface Props {
  agentConfig: AgentConfig | null;
  loading: boolean;
  error: string | null;
}

type Status =
  | { kind: "idle" }
  | { kind: "busy"; msg: string }
  | { kind: "ok"; msg: string }
  | { kind: "err"; msg: string };

// ---------------------------------------------------------------------------
// Transport link helpers
// ---------------------------------------------------------------------------
function buildTransportLink(
  mode: string,
  homeLocation: string,
  airport: AgentAirport,
): { href: string; label: string }[] {
  const origin = encodeURIComponent(homeLocation);
  const dest = encodeURIComponent(`${airport.name} airport`);
  if (mode === "vlak/bus") {
    return [{
      href: `https://www.google.com/maps/dir/?api=1&origin=${origin}&destination=${dest}&travelmode=transit`,
      label: "Trasa MHD / vlak",
    }];
  }
  if (mode === "auto") {
    return [{
      href: `https://www.google.com/maps/dir/?api=1&origin=${origin}&destination=${dest}&travelmode=driving`,
      label: "Trasa autem",
    }];
  }
  if (mode === "let") {
    return [
      { href: `https://www.google.com/flights?hl=cs#flt=MUC.${airport.code}.`, label: "Google Flights z MUC" },
      { href: `https://www.google.com/flights?hl=cs#flt=NUE.${airport.code}.`, label: "Google Flights z NUE" },
    ];
  }
  return [];
}

// ---------------------------------------------------------------------------
// Drobné stavební prvky
// ---------------------------------------------------------------------------
function Field({
  label,
  children,
  className,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1", className)}>
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}

function NumberInput({
  value,
  onChange,
  className,
  step,
  min,
}: {
  value: number;
  onChange: (n: number) => void;
  className?: string;
  step?: number | string;
  min?: number;
}) {
  return (
    <Input
      type="number"
      step={step}
      min={min}
      className={className}
      value={Number.isFinite(value) ? value : ""}
      onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
    />
  );
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm font-medium transition-colors",
        checked
          ? "border-emerald-400 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
          : "border-input bg-background text-muted-foreground hover:bg-muted",
      )}
    >
      <span>{label}</span>
      <span
        className={cn(
          "relative h-5 w-9 shrink-0 rounded-full transition-colors",
          checked ? "bg-emerald-500" : "bg-muted-foreground/40",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all",
            checked ? "left-4" : "left-0.5",
          )}
        />
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Editor řádku letiště — kompaktní jednořádkový layout
// ---------------------------------------------------------------------------
function AirportRow({
  airport,
  withTransport,
  homeLocation,
  onChange,
  onRemove,
  onSave,
  isBusy,
}: {
  airport: AgentAirport;
  withTransport: boolean;
  homeLocation?: string;
  onChange: (patch: Partial<AgentAirport>) => void;
  onRemove: () => void;
  onSave: () => void;
  isBusy: boolean;
}) {
  const t = airport.transport ?? { costEur: 0, durationMin: 0, mode: "vlak/bus" };
  const setTransport = (patch: Partial<typeof t>) =>
    onChange({ transport: { ...t, ...patch } });

  const mode = t.mode || "vlak/bus";
  const transportLinks = withTransport && homeLocation && airport.name
    ? buildTransportLink(mode, homeLocation, airport)
    : [];

  const modeLabel = (
    <span className="flex items-center gap-1">
      {mode === "let" ? "Let" : "Prostředek"}
      {transportLinks.map((l) => (
        <a key={l.href} href={l.href} target="_blank" rel="noopener noreferrer"
          title={l.label} className="text-primary hover:underline leading-none">↗</a>
      ))}
    </span>
  );

  return (
    <div className="rounded-md border bg-muted/30 px-2 py-2 flex flex-wrap items-end gap-x-2 gap-y-1">
      {/* Drag handle */}
      <span className="flex cursor-grab items-center pb-[9px] text-muted-foreground/40 hover:text-muted-foreground active:cursor-grabbing">
        <GripVertical className="h-4 w-4" />
      </span>

      {/* Identita letiště */}
      <Field label="IATA" className="w-14">
        <Input value={airport.code} maxLength={3} className="uppercase h-9"
          onChange={(e) => onChange({ code: e.target.value.toUpperCase() })} />
      </Field>
      <Field label="Název" className="w-44">
        <Input value={airport.name} className="h-9"
          onChange={(e) => onChange({ name: e.target.value })} />
      </Field>
      <div className="self-end pb-[2px]">
        <Toggle checked={airport.enabled} onChange={(v) => onChange({ enabled: v })}
          label={airport.enabled ? "aktivní" : "vyp."} />
      </div>

      {/* Doprava — oddělena svislou čarou */}
      {withTransport && (
        <>
          <div className="self-stretch w-px bg-border mx-0.5 my-0.5" />
          <Field label={modeLabel} className="w-28">
            <select value={mode} onChange={(e) => setTransport({ mode: e.target.value })}
              className="flex h-9 w-full rounded-md border border-input bg-background px-2 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
              <option value="vlak/bus">vlak / bus</option>
              <option value="auto">auto</option>
              <option value="let">let</option>
            </select>
          </Field>
          <Field label={mode === "let" ? "Let EUR" : "Cena EUR"} className="w-20">
            <NumberInput value={t.costEur} min={0}
              onChange={(n) => setTransport({ costEur: n })} />
          </Field>
          <Field label={mode === "let" ? "Let h" : "Doba h"} className="w-20">
            <NumberInput value={t.durationMin / 60} min={0} step={0.5}
              onChange={(n) => setTransport({ durationMin: n * 60 })} />
          </Field>
          {mode === "let" && (
            <>
              <Field label="Transfer EUR" className="w-24">
                <NumberInput value={t.airportTransferCostEur ?? 25} min={0}
                  onChange={(n) => setTransport({ airportTransferCostEur: n })} />
              </Field>
              <Field label="Transfer h" className="w-20">
                <NumberInput value={t.airportTransferTimeH ?? 2.5} min={0} step={0.5}
                  onChange={(n) => setTransport({ airportTransferTimeH: n })} />
              </Field>
            </>
          )}
          <Button size="sm" onClick={onSave} disabled={isBusy}
            title="Uložit na GitHub" className="self-end mb-[1px]">
            <Save className="h-4 w-4" />
          </Button>
        </>
      )}

      {/* Smazat */}
      <Button variant="ghost" size="icon" onClick={onRemove}
        title="Odebrat letiště"
        className="self-end text-destructive hover:bg-destructive/10">
        <Trash2 />
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hlavní stránka
// ---------------------------------------------------------------------------
export function SettingsPage({ agentConfig, loading, error }: Props) {
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [token, setToken] = useState<string>(() => loadToken());
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const fileInputRef = useRef<HTMLInputElement>(null);

  // D&D state — dropInsert is the index to insert before (0..n)
  const [dragState, setDragState] = useState<{
    group: "europeAirports" | "japanAirports";
    fromIdx: number;
  } | null>(null);
  const [dropInsert, setDropInsert] = useState<{
    group: "europeAirports" | "japanAirports";
    idx: number;
  } | null>(null);

  useEffect(() => {
    if (agentConfig && !config) setConfig(cloneConfig(agentConfig));
  }, [agentConfig, config]);

  const validationErrors = useMemo(
    () => (config ? validateAgentConfig(config) : []),
    [config],
  );

  if (loading) {
    return <p className="py-10 text-center text-sm text-muted-foreground">Načítám konfiguraci…</p>;
  }
  if (error || !config) {
    return (
      <Card className="border-destructive">
        <CardContent className="p-4 text-sm text-destructive">
          Nepodařilo se načíst konfiguraci agenta{error ? `: ${error}` : "."}
        </CardContent>
      </Card>
    );
  }

  const update = (mut: (draft: AgentConfig) => void) => {
    setConfig((prev) => {
      if (!prev) return prev;
      const next = cloneConfig(prev);
      mut(next);
      return next;
    });
  };

  const patchAirport = (
    group: "europeAirports" | "japanAirports",
    index: number,
    patch: Partial<AgentAirport>,
  ) => update((d) => Object.assign(d[group][index], patch));

  const removeAirport = (group: "europeAirports" | "japanAirports", index: number) =>
    update((d) => d[group].splice(index, 1));

  const addAirport = (group: "europeAirports" | "japanAirports") =>
    update((d) => {
      const nextPriority = d[group].length + 1;
      d[group].push(
        group === "europeAirports"
          ? emptyEuropeAirport(nextPriority)
          : emptyJapanAirport(nextPriority),
      );
    });

  const reorderAirports = (
    group: "europeAirports" | "japanAirports",
    fromIdx: number,
    toIdx: number,
  ) => {
    if (fromIdx === toIdx) return;
    update((d) => {
      const arr = d[group];
      const [item] = arr.splice(fromIdx, 1);
      arr.splice(toIdx, 0, item);
      arr.forEach((a, i) => { a.priority = i + 1; });
    });
  };

  const handleDrop = (group: "europeAirports" | "japanAirports") => {
    if (dragState && dragState.group === group && dropInsert?.group === group) {
      let toIdx = dropInsert.idx;
      // After removing fromIdx the slice shifts down by 1 if toIdx is after it
      if (toIdx > dragState.fromIdx) toIdx--;
      reorderAirports(group, dragState.fromIdx, toIdx);
    }
    setDragState(null);
    setDropInsert(null);
  };

  // -- akce -------------------------------------------------------------------
  const handleSaveToken = () => {
    saveToken(token);
    setStatus({ kind: "ok", msg: token ? "Token uložen do prohlížeče." : "Token smazán." });
  };

  const handleCommit = async () => {
    if (validationErrors.length) {
      setStatus({ kind: "err", msg: "Oprav chyby validace před uložením." });
      return;
    }
    if (!token) {
      setStatus({ kind: "err", msg: "Nejdřív vlož a ulož GitHub token." });
      return;
    }
    setStatus({ kind: "busy", msg: "Ukládám config na GitHub…" });
    try {
      const serialized = serializeConfig(config);
      const commitMsg = "config: úprava agenta přes dashboard (Nastavení)";
      await commitWithRetry(token, "main", serialized, commitMsg);
      try {
        await commitWithRetry(token, "gh-pages", serialized, commitMsg);
      } catch {
        /* gh-pages nemusí existovat */
      }
      setStatus({
        kind: "ok",
        msg: `Uloženo do ${AGENT_CONFIG_PATH}. Projeví se ihned po reloadu.`,
      });
    } catch (e) {
      setStatus({ kind: "err", msg: e instanceof Error ? e.message : String(e) });
    }
  };

  const handleSaveAirport = async () => {
    if (!token) {
      setStatus({ kind: "err", msg: "Nejdřív vlož a ulož GitHub token." });
      return;
    }
    setStatus({ kind: "busy", msg: "Ukládám na GitHub…" });
    try {
      const serialized = serializeConfig(config);
      await commitWithRetry(token, "main", serialized, "config: update letiště");
      try {
        await commitWithRetry(token, "gh-pages", serialized, "config: update letiště");
      } catch { /* gh-pages nemusí existovat */ }
      setStatus({ kind: "ok", msg: "Letiště uloženo ✓" });
    } catch (e) {
      setStatus({ kind: "err", msg: e instanceof Error ? e.message : String(e) });
    }
  };

  const handleTriggerScan = async () => {
    if (!token) {
      setStatus({ kind: "err", msg: "Pro spuštění scanu je potřeba token (Actions: write)." });
      return;
    }
    setStatus({ kind: "busy", msg: "Spouštím scan…" });
    try {
      await triggerScan(token);
      setStatus({ kind: "ok", msg: "Scan spuštěn (workflow_dispatch). Sleduj GitHub Actions." });
    } catch (e) {
      setStatus({ kind: "err", msg: e instanceof Error ? e.message : String(e) });
    }
  };

  const handleExport = () => {
    const blob = new Blob([serializeConfig(config)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "agent.json";
    a.click();
    URL.revokeObjectURL(url);
    setStatus({ kind: "ok", msg: "Config stažen jako agent.json." });
  };

  const handleImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result)) as AgentConfig;
        const errs = validateAgentConfig(parsed);
        if (errs.length) {
          setStatus({ kind: "err", msg: `Importovaný config je nevalidní: ${errs[0]}` });
          return;
        }
        setConfig(cloneConfig(parsed));
        setStatus({ kind: "ok", msg: "Config naimportován. Zkontroluj a ulož." });
      } catch (err) {
        setStatus({ kind: "err", msg: `Nepodařilo se načíst JSON: ${String(err)}` });
      }
    };
    reader.readAsText(file);
  };

  const busy = status.kind === "busy";

  const renderAirportList = (group: "europeAirports" | "japanAirports") => {
    const withTransport = group === "europeAirports";
    const showLine = (idx: number) =>
      dragState?.group === group &&
      dropInsert?.group === group &&
      dropInsert.idx === idx;

    return (
      <>
        {showLine(0) && <div className="h-0.5 rounded-full bg-primary" />}
        {config[group].map((a, i) => (
          <Fragment key={i}>
            <div
              draggable
              onDragStart={(e) => { e.stopPropagation(); setDragState({ group, fromIdx: i }); }}
              onDragEnd={() => { setDragState(null); setDropInsert(null); }}
              onDragOver={(e) => {
                e.preventDefault();
                const rect = e.currentTarget.getBoundingClientRect();
                setDropInsert({ group, idx: e.clientY < rect.top + rect.height / 2 ? i : i + 1 });
              }}
              onDrop={(e) => { e.preventDefault(); handleDrop(group); }}
              className={cn(dragState?.group === group && dragState.fromIdx === i && "opacity-40")}
            >
              <AirportRow
                airport={a}
                withTransport={withTransport}
                homeLocation={config.homeLocation}
                onChange={(patch) => patchAirport(group, i, patch)}
                onRemove={() => removeAirport(group, i)}
                onSave={handleSaveAirport}
                isBusy={busy}
              />
            </div>
            {showLine(i + 1) && <div className="h-0.5 rounded-full bg-primary" />}
          </Fragment>
        ))}
        <Button variant="outline" onClick={() => addAirport(group)}>
          <Plus /> Přidat {withTransport ? "evropské" : "japonské"} letiště
        </Button>
      </>
    );
  };

  return (
    <div className="space-y-5">
      {/* Obecné */}
      <Card>
        <CardHeader>
          <CardTitle>Obecné</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-4">
          <Field label="Výchozí lokace (doprava)" className="w-56">
            <Input
              value={config.homeLocation}
              onChange={(e) => update((d) => (d.homeLocation = e.target.value))}
            />
          </Field>
          <Field label="Cestovní okno – od" className="w-44">
            <Input
              type="date"
              value={config.travelWindow.from}
              onChange={(e) => update((d) => (d.travelWindow.from = e.target.value))}
            />
          </Field>
          <Field label="Cestovní okno – do" className="w-44">
            <Input
              type="date"
              value={config.travelWindow.to}
              onChange={(e) => update((d) => (d.travelWindow.to = e.target.value))}
            />
          </Field>
          <Field label="Min nocí" className="w-28">
            <NumberInput
              value={config.stayLength.minNights}
              min={0}
              onChange={(n) => update((d) => (d.stayLength.minNights = n))}
            />
          </Field>
          <Field label="Max nocí" className="w-28">
            <NumberInput
              value={config.stayLength.maxNights}
              min={0}
              onChange={(n) => update((d) => (d.stayLength.maxNights = n))}
            />
          </Field>
        </CardContent>
      </Card>

      {/* Evropská letiště */}
      <Card>
        <CardHeader>
          <CardTitle>Evropská odletová letiště</CardTitle>
          <CardDescription>
            Přetažením řádku změňte pořadí (prioritu). Deaktivovaná letiště zůstávají ve statistikách.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {renderAirportList("europeAirports")}
        </CardContent>
      </Card>

      {/* Japonská letiště */}
      <Card>
        <CardHeader>
          <CardTitle>Japonská cílová letiště</CardTitle>
          <CardDescription>
            Přetažením řádku změňte pořadí (prioritu).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {renderAirportList("japanAirports")}
        </CardContent>
      </Card>

      {/* Prahy alertů */}
      <Card>
        <CardHeader>
          <CardTitle>Prahy alertů</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-4">
          <Field label="Max cena dealu (EUR)" className="w-44">
            <NumberInput
              value={config.alertThresholds.dealMaxEur}
              min={0}
              onChange={(n) => update((d) => (d.alertThresholds.dealMaxEur = n))}
            />
          </Field>
          <Field label={`„Velký pokles“ (%)`} className="w-44">
            <NumberInput
              value={config.alertThresholds.bigDropPct}
              min={0}
              onChange={(n) => update((d) => (d.alertThresholds.bigDropPct = n))}
            />
          </Field>
          <Field label={`Citlivost „nového minima" (%)`} className="w-56">
            <NumberInput
              value={config.alertThresholds.newLowSensitivityPct}
              min={0}
              step="any"
              onChange={(n) => update((d) => (d.alertThresholds.newLowSensitivityPct = n))}
            />
          </Field>
        </CardContent>
      </Card>

      {/* Zdroje dat */}
      <Card>
        <CardHeader>
          <CardTitle>Zdroje dat / API</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Toggle label="Google Flights" checked={config.sources.googleFlights} onChange={(v) => update((d) => (d.sources.googleFlights = v))} />
          <Toggle label="Duffel" checked={config.sources.duffel} onChange={(v) => update((d) => (d.sources.duffel = v))} />
          <Toggle label="Sky Scrapper" checked={config.sources.skyScrapper} onChange={(v) => update((d) => (d.sources.skyScrapper = v))} />
          <Toggle label="SerpAPI" checked={config.sources.serpApi} onChange={(v) => update((d) => (d.sources.serpApi = v))} />
          <Toggle label="Amadeus" checked={config.sources.amadeus} onChange={(v) => update((d) => (d.sources.amadeus = v))} />
          <Toggle label="Travelpayouts" checked={config.sources.travelpayouts} onChange={(v) => update((d) => (d.sources.travelpayouts = v))} />
          <Toggle label="FlightLabs" checked={config.sources.flightLabs} onChange={(v) => update((d) => (d.sources.flightLabs = v))} />
          <Toggle label="LetsFG" checked={config.sources.letsFG} onChange={(v) => update((d) => (d.sources.letsFG = v))} />
          <Toggle label="RSS: Secret Flying" checked={config.sources.rss.secretFlying} onChange={(v) => update((d) => (d.sources.rss.secretFlying = v))} />
          <Toggle label="RSS: Cestujlevně" checked={config.sources.rss.cestujlevne} onChange={(v) => update((d) => (d.sources.rss.cestujlevne = v))} />
          <Toggle label="RSS: Jack's Flight Club" checked={config.sources.rss.jacks} onChange={(v) => update((d) => (d.sources.rss.jacks = v))} />
          <Toggle label="RSS: Miles & More" checked={config.sources.rss.milesAndMore} onChange={(v) => update((d) => (d.sources.rss.milesAndMore = v))} />
        </CardContent>
      </Card>

      {/* Telegram alerty */}
      <Card>
        <CardHeader>
          <CardTitle>Telegram alerty</CardTitle>
          <CardDescription>
            Telegram zůstává jen alertovací kanál (nové minimum, velký pokles, deal).
          </CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Toggle label="Cenový alert" checked={config.telegramAlerts.priceAlert} onChange={(v) => update((d) => (d.telegramAlerts.priceAlert = v))} />
          <Toggle label="Deal alert" checked={config.telegramAlerts.dealAlert} onChange={(v) => update((d) => (d.telegramAlerts.dealAlert = v))} />
          <Toggle label="Denní souhrn" checked={config.telegramAlerts.dailySummary} onChange={(v) => update((d) => (d.telegramAlerts.dailySummary = v))} />
        </CardContent>
      </Card>

      {/* GitHub přístup a akce */}
      <Card>
        <CardHeader>
          <CardTitle>GitHub přístup a akce</CardTitle>
          <CardDescription>
            <strong>Save u letiště</strong> — uloží jen toto letiště.
            <br />
            <strong>Uložit konfiguraci</strong> — commitne celou konfiguraci.
            <br />
            <strong>Spustit scan</strong> — spustí scan hned (místo čekání na cron).
            <br />
            <strong>Export/Import</strong> — ručná archivace bez GitHubu.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Personal Access Token" className="min-w-72 flex-1">
              <Input
                type="password"
                placeholder="github_pat_…"
                value={token}
                autoComplete="off"
                onChange={(e) => setToken(e.target.value)}
              />
            </Field>
            <Button variant="outline" onClick={handleSaveToken}>
              Uložit token
            </Button>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button onClick={handleCommit} disabled={busy || validationErrors.length > 0}>
              <Save /> Uložit konfiguraci na GitHub
            </Button>
            <Button variant="secondary" onClick={handleTriggerScan} disabled={busy}>
              <Play /> Spustit scan teď
            </Button>
            <Button variant="outline" onClick={handleExport}>
              <Download /> Export JSON
            </Button>
            <Button variant="outline" onClick={() => fileInputRef.current?.click()}>
              <Upload /> Import JSON
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={handleImport}
            />
          </div>
          {status.kind !== "idle" && (
            <p
              className={cn(
                "text-sm",
                status.kind === "ok" && "text-emerald-700 dark:text-emerald-400",
                status.kind === "err" && "text-destructive",
                status.kind === "busy" && "text-muted-foreground",
              )}
            >
              {status.msg}
            </p>
          )}
          {validationErrors.length > 0 && (
            <div className="rounded-md border border-amber-400 bg-amber-50 p-3 text-sm dark:border-amber-700 dark:bg-amber-950">
              <p className="mb-1 font-medium text-amber-800 dark:text-amber-300">
                Konfigurace má {validationErrors.length}{" "}
                {validationErrors.length === 1 ? "chybu" : "chyb/y"} – ulož nepůjde:
              </p>
              <ul className="list-disc space-y-0.5 pl-5 text-amber-800 dark:text-amber-300">
                {validationErrors.map((err, i) => (
                  <li key={i}>{err}</li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
