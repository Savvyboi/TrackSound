import { useSyncExternalStore } from "react";

/** A minimal external store with selector hooks (enough for this app; no extra dependency). */
export type Store<T> = {
  get: () => T;
  set: (patch: Partial<T> | ((s: T) => Partial<T>)) => void;
  subscribe: (fn: () => void) => () => void;
  use: <U>(selector: (s: T) => U) => U;
};

export function createStore<T extends object>(initial: T): Store<T> {
  let state = initial;
  const listeners = new Set<() => void>();
  const get = () => state;
  const set: Store<T>["set"] = (patch) => {
    const p = typeof patch === "function" ? patch(state) : patch;
    let changed = false;
    for (const k in p) {
      if (!Object.is((state as Record<string, unknown>)[k], (p as Record<string, unknown>)[k])) {
        changed = true;
        break;
      }
    }
    if (!changed) return;
    state = { ...state, ...p };
    listeners.forEach((l) => l());
  };
  const subscribe = (fn: () => void) => {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  };
  function use<U>(selector: (s: T) => U): U {
    return useSyncExternalStore(
      subscribe,
      () => selector(state),
      () => selector(state),
    );
  }
  return { get, set, subscribe, use };
}

/** localStorage helpers that never throw (private mode, blocked storage, ...). */
export function loadPref<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function savePref(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}
