import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // В разработке консоль живёт на 5173, а сервер — на 8000. Прокси
    // делает их одним источником для браузера, иначе cookie сессии
    // с SameSite=Strict до сервера не доедет.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
