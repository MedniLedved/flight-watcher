import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Statický build pro GitHub Pages – relativní base, ať funguje i v podsložce.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    // Recharts + Leaflet + React ≈ 850 KB minified; limit zvednut pro SPA bez lazy-loadingu.
    chunkSizeWarningLimit: 900,
  },
});
