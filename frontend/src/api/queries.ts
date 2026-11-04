import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./client";
import type {
  AircraftEvent,
  AlertsState,
  AircraftSearch,
  Case,
  CandidateCurves,
  CasePatch,
  EnfMatch,
  EnfReference,
  EnfState,
  FlightSet,
  FlightsGeoJSON,
  GridMapGeoJSON,
  Health,
  Job,
  LightningSolve,
  LightningState,
  Strike,
  ThunderEnvelope,
  Weather,
} from "./types";

export const qk = {
  health: ["health"] as const,
  cases: ["cases"] as const,
  case: (id: string) => ["case", id] as const,
  jobs: ["jobs"] as const,
  aircraft: (id: string) => ["aircraft", id] as const,
  events: (id: string) => ["aircraft", id, "events"] as const,
  flights: (id: string) => ["aircraft", id, "flights"] as const,
  flightsGeo: (id: string, t0?: number | null, t1?: number | null) => ["aircraft", id, "flights-geojson", t0 ?? null, t1 ?? null] as const,
  searches: (id: string) => ["aircraft", id, "searches"] as const,
  search: (id: string, sid: string) => ["aircraft", id, "search", sid] as const,
  curves: (id: string, sid: string, rank: number) => ["aircraft", id, "curves", sid, rank] as const,
  lightning: (id: string) => ["lightning", id] as const,
  lightningState: (id: string) => ["lightning", id, "state"] as const,
  envelope: (id: string, t0: number, t1: number, n: number) => ["lightning", id, "envelope", t0, t1, n] as const,
  strikes: (id: string) => ["lightning", id, "strikes"] as const,
  solve: (id: string, sid: string) => ["lightning", id, "solve", sid] as const,
  enf: (id: string) => ["enf", id] as const,
  enfState: (id: string) => ["enf", id, "state"] as const,
  enfMatch: (id: string, mid: string) => ["enf", id, "match", mid] as const,
  weather: (id: string, date: string | null, region: unknown) => ["weather", id, date, JSON.stringify(region)] as const,
  enfRefs: ["enf-references"] as const,
  alerts: (id: string) => ["alerts", id] as const,
  gridMap: ["enf-grid-map"] as const,
};

/** Don't hammer endpoints that answered with a client error (e.g. not implemented yet). */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  if (error instanceof ApiError && error.status === 501) return false;
  return failureCount < 2;
}

const enc = encodeURIComponent;
export const casePath = (id: string) => `/api/cases/${enc(id)}`;

// ---- general ----------------------------------------------------------------------------

export function useHealth() {
  return useQuery({ queryKey: qk.health, queryFn: () => api.get<Health>("/api/health"), staleTime: 60_000 });
}

export function useCases() {
  return useQuery({
    queryKey: qk.cases,
    queryFn: () => api.get<Case[]>("/api/cases"),
    refetchInterval: (q) => ((q.state.data ?? []).some((c) => c.recording?.status === "processing") ? 2000 : false),
  });
}

export function useCase(id: string | undefined) {
  return useQuery({
    queryKey: qk.case(id ?? ""),
    queryFn: () => api.get<Case>(casePath(id!)),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.recording?.status === "processing" ? 2000 : false),
  });
}

export function setCaseData(qc: QueryClient, c: Case) {
  qc.setQueryData(qk.case(c.id), c);
  qc.setQueryData<Case[]>(qk.cases, (list) => {
    if (!list) return list;
    const i = list.findIndex((x) => x.id === c.id);
    if (i < 0) return [c, ...list];
    const next = list.slice();
    next[i] = c;
    return next;
  });
}

export function usePatchCase(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: CasePatch) => api.patch<Case>(casePath(id), patch),
    onMutate: async (patch) => {
      // optimistic: the header and forms update immediately
      await qc.cancelQueries({ queryKey: qk.case(id) });
      const prev = qc.getQueryData<Case>(qk.case(id));
      if (prev) {
        const next = { ...prev, ...patch } as Case;
        if ("start_utc" in patch && patch.start_utc != null && !("start_utc_source" in patch)) next.start_utc_source = "user";
        if ("start_utc" in patch && patch.start_utc == null) next.start_utc_source = null;
        qc.setQueryData(qk.case(id), next);
      }
      return { prev };
    },
    onError: (_e, _p, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.case(id), ctx.prev);
    },
    onSuccess: (c) => setCaseData(qc, c),
  });
}

export function useJobsQuery() {
  return useQuery({
    queryKey: qk.jobs,
    queryFn: () => api.get<Job[]>("/api/jobs"),
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => j.status === "queued" || j.status === "running") ? 900 : 5000),
    refetchIntervalInBackground: false,
  });
}

export function useCaseJobs(caseId: string | undefined): Job[] {
  const { data } = useJobsQuery();
  if (!caseId || !data) return [];
  return data.filter((j) => j.case_id === caseId);
}

// ---- aircraft ---------------------------------------------------------------------------

export function useAircraftEvents(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.events(id),
    queryFn: () => api.get<AircraftEvent[]>(`${casePath(id)}/aircraft/events`),
    enabled,
    retry: shouldRetry,
  });
}

export function useFlightSet(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.flights(id),
    queryFn: () => api.get<FlightSet>(`${casePath(id)}/aircraft/flights`),
    enabled,
    retry: shouldRetry,
  });
}

export function useFlightsGeo(id: string, enabled: boolean, t0?: number | null, t1?: number | null) {
  return useQuery({
    queryKey: qk.flightsGeo(id, t0, t1),
    queryFn: () =>
      api.get<FlightsGeoJSON>(`${casePath(id)}/aircraft/flights/geojson`, { t0_utc: t0 ?? undefined, t1_utc: t1 ?? undefined }),
    enabled,
    retry: shouldRetry,
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  });
}

export function useSearches(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.searches(id),
    queryFn: () => api.get<AircraftSearch[]>(`${casePath(id)}/aircraft/searches`),
    enabled,
    retry: shouldRetry,
  });
}

export function useSearch(id: string, sid: string | null) {
  return useQuery({
    queryKey: qk.search(id, sid ?? ""),
    queryFn: () => api.get<AircraftSearch>(`${casePath(id)}/aircraft/searches/${enc(sid!)}`),
    enabled: !!sid,
    retry: shouldRetry,
    staleTime: Infinity,
  });
}

export function useCandidateCurves(id: string, sid: string | null, rank: number | null) {
  return useQuery({
    queryKey: qk.curves(id, sid ?? "", rank ?? 0),
    queryFn: () => api.get<CandidateCurves>(`${casePath(id)}/aircraft/searches/${enc(sid!)}/candidates/${rank}/curves`),
    enabled: !!sid && rank != null,
    retry: shouldRetry,
    staleTime: Infinity,
  });
}

// ---- lightning --------------------------------------------------------------------------

export function useLightning(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.lightningState(id),
    queryFn: () => api.get<LightningState>(`${casePath(id)}/lightning`),
    enabled,
    retry: shouldRetry,
  });
}

export function useEnvelope(id: string, t0: number, t1: number, maxPoints: number, enabled: boolean) {
  return useQuery({
    queryKey: qk.envelope(id, t0, t1, maxPoints),
    queryFn: ({ signal }) =>
      api.get<ThunderEnvelope>(`${casePath(id)}/lightning/envelope`, { t0, t1, max_points: maxPoints }, signal),
    enabled,
    retry: shouldRetry,
    staleTime: Infinity,
    placeholderData: keepPreviousData,
  });
}

export function useStrikes(id: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.strikes(id),
    queryFn: () => api.get<{ strikes: Strike[] }>(`${casePath(id)}/lightning/strikes`),
    enabled,
    retry: shouldRetry,
  });
}

export function useSolve(id: string, sid: string | null) {
  return useQuery({
    queryKey: qk.solve(id, sid ?? ""),
    queryFn: () => api.get<LightningSolve>(`${casePath(id)}/lightning/solves/${enc(sid!)}`),
    enabled: !!sid,
    retry: shouldRetry,
    staleTime: Infinity,
  });
}

// ---- ENF --------------------------------------------------------------------------------

export function useEnf(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.enfState(id),
    queryFn: () => api.get<EnfState>(`${casePath(id)}/enf`),
    enabled,
    retry: shouldRetry,
  });
}

export function useEnfMatch(id: string, mid: string | null) {
  return useQuery({
    queryKey: qk.enfMatch(id, mid ?? ""),
    queryFn: () => api.get<EnfMatch>(`${casePath(id)}/enf/matches/${enc(mid!)}`),
    enabled: !!mid,
    retry: shouldRetry,
    staleTime: Infinity,
  });
}

export function useEnfReferences(enabled = true) {
  return useQuery({
    queryKey: qk.enfRefs,
    queryFn: () => api.get<EnfReference[]>("/api/enf/references"),
    enabled,
    retry: shouldRetry,
    staleTime: 60_000,
  });
}

export function useGridMap(enabled: boolean) {
  return useQuery({
    queryKey: qk.gridMap,
    queryFn: () => api.get<GridMapGeoJSON>("/api/enf/grid-map"),
    enabled,
    retry: shouldRetry,
    staleTime: Infinity,
  });
}

// ---- weather ----------------------------------------------------------------------------

export function useWeather(c: Case, enabled: boolean) {
  return useQuery({
    queryKey: qk.weather(c.id, c.date, c.region),
    queryFn: () => api.get<Weather>(`${casePath(c.id)}/weather`),
    enabled: enabled && !!c.date && !!c.region,
    retry: false,
    staleTime: 30 * 60_000,
  });
}

// ---- alerts -----------------------------------------------------------------------------

export function useAlerts(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.alerts(id),
    queryFn: () => api.get<AlertsState>(`${casePath(id)}/alerts`),
    enabled,
    retry: shouldRetry,
  });
}
