import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build output lands in `dist/`, which the FastAPI app mounts at /workstation when it
// exists (`api/app.py`). That means the demo needs node ONCE, to build - never at run
// time, and never five minutes before going on stage (the same worry that made the
// customer simulator build-free in `D47`).
export default defineConfig({
  base: "/workstation/",
  plugins: [react()],
  server: {
    // `vite dev` proxies the API so the session cookie is same-origin. Without this the
    // cookie is cross-site, SameSite=Lax drops it, and every request is a 401 that looks
    // like an auth bug rather than a proxy one.
    proxy: {
      "/v1": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/v1/agent/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
