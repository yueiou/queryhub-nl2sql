import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 构建产物落到 frontend/dist，由 FastAPI 的 /static 挂载托管
export default defineConfig({
  plugins: [vue()],
  base: "/static/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/static/data": "http://127.0.0.1:8765",
    },
  },
});
