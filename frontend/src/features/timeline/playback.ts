import { ApiError, describeDetail, errorMessage } from "../../api/client";
import { toast } from "../../components/ui/toast";
import { setView, ws, type Speed } from "../../state/workspace";

export const MAX_PLAY_S = 600;

let audio: HTMLAudioElement | null = null;
let objectUrl: string | null = null;
let raf = 0;
let abort: AbortController | null = null;
let current: { t0: number; t1: number; speed: number } | null = null;

function tick() {
  if (!audio || !current) return;
  const t = current.t0 + audio.currentTime * current.speed;
  const pb = ws.get().playback;
  ws.set({ playback: { ...pb, t } });
  const s = ws.get();
  if (s.followPlayhead && pb.state === "playing") {
    const v = s.view;
    const span = v.t1 - v.t0;
    if (t > v.t1 || t < v.t0) setView(t - span * 0.05, t + span * 0.95);
  }
  raf = requestAnimationFrame(tick);
}

/** Play [t0, t1] of the recording (at most 600 s) at the given speed. */
export async function playRange(caseId: string, t0: number, t1: number, speed: Speed) {
  stopPlayback();
  const a = Math.max(0, t0);
  const b = Math.min(t1, a + MAX_PLAY_S);
  if (b - a < 0.2) return;
  current = { t0: a, t1: b, speed };
  ws.set({ playback: { state: "loading", t: a, range: [a, b] } });
  const ctrl = new AbortController();
  abort = ctrl;
  try {
    const url = `/api/cases/${encodeURIComponent(caseId)}/audio?t0=${a.toFixed(3)}&t1=${b.toFixed(3)}&speed=${speed}`;
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) {
      let detail: unknown = res.statusText;
      try {
        detail = (await res.json()).detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, detail, url);
    }
    const blob = await res.blob();
    if (ctrl.signal.aborted) return;
    objectUrl = URL.createObjectURL(blob);
    audio = new Audio(objectUrl);
    audio.onended = () => stopPlayback();
    await audio.play();
    ws.set({ playback: { state: "playing", t: a, range: [a, b] } });
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(tick);
  } catch (e) {
    if ((e as Error)?.name === "AbortError" || ctrl.signal.aborted) return;
    stopPlayback();
    toast({
      tone: "error",
      title: "Playback failed",
      body: e instanceof ApiError ? describeDetail(e.detail) || e.message : errorMessage(e),
    });
  }
}

export function pausePlayback() {
  if (!audio) return;
  audio.pause();
  cancelAnimationFrame(raf);
  ws.set({ playback: { ...ws.get().playback, state: "paused" } });
}

export function resumePlayback() {
  if (!audio) return;
  void audio.play();
  ws.set({ playback: { ...ws.get().playback, state: "playing" } });
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(tick);
}

export function stopPlayback() {
  cancelAnimationFrame(raf);
  abort?.abort();
  abort = null;
  if (audio) {
    audio.pause();
    audio.onended = null;
  }
  audio = null;
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = null;
  current = null;
  const pb = ws.get().playback;
  if (pb.state !== "idle" || pb.range) ws.set({ playback: { state: "idle", t: null, range: null } });
}
