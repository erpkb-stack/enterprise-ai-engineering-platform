/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

const API = process.env.AIECC_API_URL ?? "http://localhost:8100";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    port: 5180,
    proxy: {
      "/api/v1/ws": { target: API.replace("http", "ws"), ws: true },
      "/api": { target: API, changeOrigin: true },
      "/health": API,
      "/ready": API,
    },
  },
  build: {
    chunkSizeWarningLimit: 6000,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("monaco-editor")) return "monaco";
          if (id.includes("mermaid") || id.includes("cytoscape") || id.includes("katex")) return "mermaid";
          if (id.includes("recharts") || id.includes("d3-")) return "charts";
          if (id.includes("@xyflow")) return "flow";
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
