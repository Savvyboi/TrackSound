import { Activity, ClipboardList, Plane, RadioTower, Settings2, Zap } from "lucide-react";
import type { ReactNode } from "react";
import { isActive, jobSource } from "../../api/jobs";
import { useAlerts, useCaseJobs, useEnf, useLightning, useSearches } from "../../api/queries";
import { Spinner } from "../../components/ui/misc";
import { cn } from "../../lib/cn";
import { setTab, useWs, type Tab } from "../../state/workspace";
import { AircraftTab } from "../aircraft/AircraftTab";
import { AlertsTab } from "../alerts/AlertsTab";
import { EnfTab } from "../enf/EnfTab";
import { LightningTab } from "../lightning/LightningTab";
import { SetupTab } from "../setup/SetupTab";
import { SummaryTab } from "../summary/SummaryTab";
import { useCaseData } from "./caseContext";

const TAB_META: Record<Tab, { label: string; icon: ReactNode; key: string; tip?: string }> = {
  setup: { label: "Setup", icon: <Settings2 />, key: "1" },
  aircraft: { label: "Aircraft", icon: <Plane />, key: "2" },
  lightning: { label: "Lightning", icon: <Zap />, key: "3", tip: "Lightning & events" },
  enf: { label: "Mains hum", icon: <Activity />, key: "4" },
  alerts: { label: "Alerts", icon: <RadioTower />, key: "5" },
  summary: { label: "Summary", icon: <ClipboardList />, key: "6" },
};

export function Sidebar() {
  const c = useCaseData();
  const tab = useWs((s) => s.tab);
  const jobs = useCaseJobs(c.id).filter(isActive);
  const searches = useSearches(c.id);
  const lightning = useLightning(c.id);
  const enf = useEnf(c.id);
  const alerts = useAlerts(c.id);
  const busy = (src: string) => jobs.some((j) => jobSource(j.kind) === src);
  const status: Record<Tab, "busy" | "done" | "todo" | null> = {
    setup: !c.date || !c.region ? "todo" : null,
    aircraft: busy("aircraft") ? "busy" : searches.data?.some((s) => s.best) ? "done" : null,
    lightning: busy("lightning") ? "busy" : lightning.data?.solves?.some((s) => s.best) ? "done" : null,
    enf: busy("enf") ? "busy" : enf.data?.matches?.some((m) => m.best) ? "done" : null,
    alerts: busy("alerts") ? "busy" : alerts.data?.headers?.length ? "done" : null,
    summary: null,
  };

  return (
    <aside className="flex h-full flex-col border-l border-line bg-surface" aria-label="Analysis">
      <nav role="tablist" className="flex shrink-0 gap-0.5 border-b border-line px-1.5 pt-1.5">
        {(Object.keys(TAB_META) as Tab[]).map((t) => {
          const m = TAB_META[t];
          const st = status[t];
          return (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              data-tip={`${m.tip ?? m.label} (${m.key})`}
              className={cn(
                "relative flex min-w-0 flex-1 flex-col items-center gap-1 rounded-t-md px-0.5 pt-2 pb-2 text-2xs font-medium transition-colors [&_svg]:size-4",
                tab === t ? "text-fg" : "text-fg-faint hover:bg-surface-2 hover:text-fg-muted",
              )}
            >
              <span className={cn("relative", tab === t && "text-accent")}>
                {m.icon}
                {st && (
                  <span className="absolute -top-1 -right-1.5">
                    {st === "busy" ? (
                      <Spinner className="size-2.5 text-accent" />
                    ) : (
                      <span className={cn("block size-1.5 rounded-full ring-2 ring-surface", st === "done" ? "bg-ok" : "bg-warn")} />
                    )}
                  </span>
                )}
              </span>
              <span className="whitespace-nowrap">{m.label}</span>
              {tab === t && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-accent" />}
            </button>
          );
        })}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto" role="tabpanel">
        {tab === "setup" && <SetupTab />}
        {tab === "aircraft" && <AircraftTab />}
        {tab === "lightning" && <LightningTab />}
        {tab === "enf" && <EnfTab />}
        {tab === "alerts" && <AlertsTab />}
        {tab === "summary" && <SummaryTab />}
      </div>
    </aside>
  );
}
