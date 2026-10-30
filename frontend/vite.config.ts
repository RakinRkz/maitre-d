import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `/api` is proxied so the browser sees one origin, as it does in the built app, which the
// backend serves itself. That also keeps the backend's local-only check meaningful: the proxy
// connects from localhost.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://localhost:8000" },
  },
  build: {
    // The one chunk over the default 500 kB is the voice SDK with its WebRTC library, loaded on
    // demand (see App.tsx); measured at 611 kB minified, 161 kB gzipped. The limit sits just
    // above it so that a different chunk growing past it still warns.
    chunkSizeWarningLimit: 650,
  },
  test: {
    // Everything under test is plain logic; nothing needs a DOM.
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
