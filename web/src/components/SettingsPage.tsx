import { useEffect, useMemo, useRef, useState } from "react";
import { Download, Play, Plus, Save, Trash2, Upload } from "lucide-react";

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
  commitAgentConfig,
  fetchAgentConfigFile,
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
// Drobné stavební prvky
// ---------------------------------------------------------------------------
function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1", className)}>
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
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
// Editor řádku letiště
// ---------------------------------------------------------------------------
function AirportRow({
  airport,
  withTransport,
  onChange,
  onRemove,
  onSave,
  isBusy,
}: {
  airport: AgentAirport;
  withTransport: boolean;
  onChange: (patch: Partial<AgentAirport>) => void;
  onRemove: () => void;
  onSave: () => void;
  isBusy: boolean;
}) {
  const t = airport.transport ?? { costEur: 0, durationMin: 0, mode: "" };
  const setTransport = (patch: Partial<typeof t>) =>
    onChange({ transport: { ...t, ...patch } });

  return (
    <div className="rounded-md border bg-muted/30 p-3">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="IATA" className="w-20">
          <Input
            value={airport.code}
            maxLength={3}
            className="uppercase"
            onChange={(e) => onChange({ code: e.target.value.toUpperCase() })}
          />
        </Field>
        <Field label="Název" className="min-w-40 flex-1">
          <Input value={airport.name} onChange={(e) => onChange({ name: e.target.value })} />
        </Field>
        <Field label="Lat" className="w-28">
          <NumberInput
            value={airport.lat}
            step="any"
            onChange={(n) => onChange({ lat: n })}
          />
        </Field>
        <Field label="Lon" className="w-28">
          <NumberInput
            value={airport.lon}
            step="any"
            onChange={(n) => onChange({ lon: n })}
          />
        </Field>
        <Field label="Priorita" className="w-24">
          <NumberInput
            value={airport.priority}
            min={1}
            onChange={(n) => onChange({ priority: n })}
          />
        </Field>
        <Toggle
          checked={airport.enabled}
          onChange={(v) => onChange({ enabled: v })}
          label={airport.enabled ? "aktivní" : "vypnuté"}
        />
        <Button
          variant="ghost"
          size="icon"
          onClick={onRemove}
          title="Odebrat letiště"
          className="text-destructive hover:bg-destructive/10"
        >
          <Trash2 />
        </Button>
      </div>

      {withTransport && (
        <div className="mt-3 flex flex-wrap items-end gap-3 border-t pt-3">
          <span className="text-xs font-medium text-muted-foreground">
            Doprava z domova:
          </span>
          <Field label="Cena (EUR)" className="w-28">
            <NumberInput
              value={t.costEur}
              min={0}
              onChange={(n) => setTransport({ costEur: n })}
            />
          </Field>
          <Field label="Doba (h)" className="w-28">
            <NumberInput
              value={t.durationMin / 60}
              min={0}
              step={0.5}
              onChange={(n) => setTransport({ durationMin: n * 60 })}
            />
          </Field>
          <Field label="Prostředek" className="min-w-40 flex-1">
            <Input value={t.mode} onChange={(e) => setTransport({ mode: e.target.value })} />
          </Field>
          <Button size="sm" onClick={onSave} disabled={isBusy} title="Uložit toto letiště na GitHub">
            <Save className="h-4 w-4" />
          </Button>
        </div>
      )}
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

  // Inicializuj pracovní kopii z načteného configu (jen jednou, ať se
  // rozpracované změny nepřepíšou).
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

  // -- mutace pracovní kopie --------------------------------------------------
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
      const nextPriority = d[group].reduce((m, a) => Math.max(m, a.priority), 0) + 1;
      d[group].push(
        group === "europeAirports"
          ? emptyEuropeAirport(nextPriority)
          : emptyJapanAirport(nextPriority),
      );
    });

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

      // 1) main — scanner čte config odsud při každém běhu
      const remoteMain = await fetchAgentConfigFile(token, "main");
      await commitAgentConfig(token, serialized, remoteMain.sha, commitMsg, "main");

      // 2) gh-pages — dashboard čte config odsud; okamžitý efekt po reloadu
      try {
        const remotePages = await fetchAgentConfigFile(token, "gh-pages");
        await commitAgentConfig(token, serialized, remotePages.sha, commitMsg, "gh-pages");
      } catch {
        // gh-pages nemusí mít soubor (první deploy) — nevadí, main stačí
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
      const remoteMain = await fetchAgentConfigFile(token, "main");
      await commitAgentConfig(token, serialized, remoteMain.sha, "config: update letiště", "main");
      try {
        const remotePages = await fetchAgentConfigFile(token, "gh-pages");
        await commitAgentConfig(token, serialized, remotePages.sha, "config: update letiště", "gh-pages");
      } catch {
        /* nevadí */
      }
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
    e.target.value = ""; // umožni načíst stejný soubor znovu
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
            Priorita = pořadí preference (1 = nejvyšší). „Doprava" se připočítává 2× při
            zobrazení „cena vč. dopravy".
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {config.europeAirports.map((a, i) => (
            <AirportRow
              key={i}
              airport={a}
              withTransport
              onChange={(patch) => patchAirport("europeAirports", i, patch)}
              onRemove={() => removeAirport("europeAirports", i)}
              onSave={handleSaveAirport}
              isBusy={busy}
            />
          ))}
          <Button variant="outline" onClick={() => addAirport("europeAirports")}>
            <Plus /> Přidat evropské letiště
          </Button>
        </CardContent>
      </Card>

      {/* Japonská letiště */}
      <Card>
        <CardHeader>
          <CardTitle>Japonská cílová letiště</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {config.japanAirports.map((a, i) => (
            <AirportRow
              key={i}
              airport={a}
              withTransport={false}
              onChange={(patch) => patchAirport("japanAirports", i, patch)}
              onRemove={() => removeAirport("japanAirports", i)}
              onSave={handleSaveAirport}
              isBusy={busy}
            />
          ))}
          <Button variant="outline" onClick={() => addAirport("japanAirports")}>
            <Plus /> Přidat japonské letiště
          </Button>
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
          <Field label="„Velký pokles“ (%)" className="w-44">
            <NumberInput
              value={config.alertThresholds.bigDropPct}
              min={0}
              onChange={(n) => update((d) => (d.alertThresholds.bigDropPct = n))}
            />
          </Field>
          <Field label="Citlivost „nového minima“ (%)" className="w-56">
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
          <Toggle
            label="Google Flights"
            checked={config.sources.googleFlights}
            onChange={(v) => update((d) => (d.sources.googleFlights = v))}
          />
          <Toggle
            label="Duffel"
            checked={config.sources.duffel}
            onChange={(v) => update((d) => (d.sources.duffel = v))}
          />
          <Toggle
            label="Sky Scrapper"
            checked={config.sources.skyScrapper}
            onChange={(v) => update((d) => (d.sources.skyScrapper = v))}
          />
          <Toggle
            label="SerpAPI"
            checked={config.sources.serpApi}
            onChange={(v) => update((d) => (d.sources.serpApi = v))}
          />
          <Toggle
            label="Amadeus"
            checked={config.sources.amadeus}
            onChange={(v) => update((d) => (d.sources.amadeus = v))}
          />
          <Toggle
            label="Travelpayouts"
            checked={config.sources.travelpayouts}
            onChange={(v) => update((d) => (d.sources.travelpayouts = v))}
          />
          <Toggle
            label="FlightLabs"
            checked={config.sources.flightLabs}
            onChange={(v) => update((d) => (d.sources.flightLabs = v))}
          />
          <Toggle
            label="LetsFG"
            checked={config.sources.letsFG}
            onChange={(v) => update((d) => (d.sources.letsFG = v))}
          />
          <Toggle
            label="RSS: Secret Flying"
            checked={config.sources.rss.secretFlying}
            onChange={(v) => update((d) => (d.sources.rss.secretFlying = v))}
          />
          <Toggle
            label="RSS: Cestujlevně"
            checked={config.sources.rss.cestujlevne}
            onChange={(v) => update((d) => (d.sources.rss.cestujlevne = v))}
          />
          <Toggle
            label="RSS: Jack's Flight Club"
            checked={config.sources.rss.jacks}
            onChange={(v) => update((d) => (d.sources.rss.jacks = v))}
          />
          <Toggle
            label="RSS: Miles & More"
            checked={config.sources.rss.milesAndMore}
            onChange={(v) => update((d) => (d.sources.rss.milesAndMore = v))}
          />
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
          <Toggle
            label="Cenový alert"
            checked={config.telegramAlerts.priceAlert}
            onChange={(v) => update((d) => (d.telegramAlerts.priceAlert = v))}
          />
          <Toggle
            label="Deal alert"
            checked={config.telegramAlerts.dealAlert}
            onChange={(v) => update((d) => (d.telegramAlerts.dealAlert = v))}
          />
          <Toggle
            label="Denní souhrn"
            checked={config.telegramAlerts.dailySummary}
            onChange={(v) => update((d) => (d.telegramAlerts.dailySummary = v))}
          />
        </CardContent>
      </Card>

      {/* GitHub přístup a akce */}
      <Card>
        <CardHeader>
          <CardTitle>GitHub přístup a akce</CardTitle>
          <CardDescription>
            <strong>Tlačítko Save u každého letiště</strong> — uloží jen toto letiště (doprava, cena).
            <br />
            <strong>Uložit konfiguraci</strong> — commitne celou konfiguraci.
            <br />
            <strong>Spustit scan</strong> — spustí scan hned (místo čekání na cron).
            <br />
            <strong>Export/Import</strong> — pro ručnu archivaci či přesun bez GitHubu.
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
