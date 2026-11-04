import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `npm run dev` proxies /api to the TrackSound server. Point it elsewhere with
// VITE_API_TARGET, e.g. `VITE_API_TARGET=http://127.0.0.1:8100 npm run dev`.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_TARGET || "http://127.0.0.1:8000";
  const proxy = { "/api": { target, changeOrigin: true } };
  return {
    plugins: [react(), tailwindcss()],
    server: { port: 5173, proxy },
    preview: { port: 4173, proxy },
    build: {
      outDir: "dist",
      sourcemap: true,
      chunkSizeWarningLimit: 1600,
    },
  };
});
