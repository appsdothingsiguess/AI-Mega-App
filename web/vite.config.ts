import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_TARGET = "http://127.0.0.1:8000";

/** Forward all Prompter backend routes to FastAPI during `npm run dev`. */
function proxyApi() {
  return {
    target: API_TARGET,
    changeOrigin: true,
  };
}

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": proxyApi(),
      "/health": proxyApi(),
      "/settings": proxyApi(),
      "/projects": proxyApi(),
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
