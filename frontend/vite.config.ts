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
    // The build goes straight into the Python package, so `tracksound serve`, the wheel and the
    // standalone apps all ship the same UI. The built files are committed (CI checks they are up
    // to date), which lets people install from GitHub without Node.
    build: {
      outDir: "../backend/src/tracksound/web",
      emptyOutDir: true,
      sourcemap: false,
      chunkSizeWarningLimit: 1600,
    },
  };
});
