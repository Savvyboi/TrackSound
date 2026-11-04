import { createStore } from "./store";

export type Theme = "dark" | "light";

function initialTheme(): Theme {
  const t = document.documentElement.dataset.theme;
  return t === "light" ? "light" : "dark";
}

export const themeStore = createStore<{ theme: Theme }>({ theme: initialTheme() });

export function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("ts.theme", theme);
  } catch {
    /* ignore */
  }
  themeStore.set({ theme });
}

export function toggleTheme() {
  setTheme(themeStore.get().theme === "dark" ? "light" : "dark");
}

export const useTheme = () => themeStore.use((s) => s.theme);
