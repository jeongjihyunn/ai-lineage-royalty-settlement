import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api 요청은 FastAPI(app/api/server.py, 8000번)로 넘긴다.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
