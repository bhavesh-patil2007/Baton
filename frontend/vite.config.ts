import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    headers: {
      "Cache-Control": "no-store, no-cache, must-revalidate",
    },
  },
  build: {
    // Three.js' minified vendor chunk is ~533 kB (about 133 kB gzip) and is lazy-loaded
    // only when a dashboard hero mounts, so allow that known vendor-sized chunk.
    chunkSizeWarningLimit: 550,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("/node_modules/three/")) return "three-vendor";
          if (id.includes("/node_modules/framer-motion/") || id.includes("/node_modules/motion-")) {
            return "motion-vendor";
          }
          if (
            id.includes("/node_modules/react/") ||
            id.includes("/node_modules/react-dom/") ||
            id.includes("/node_modules/scheduler/")
          ) {
            return "react-vendor";
          }
        },
      },
    },
  },
  test: { environment: "jsdom", globals: true, setupFiles: "./src/test-setup.ts", css: false },
});
