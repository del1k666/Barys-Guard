// Настройки тестов вынесены из vite.config.ts: vitest 2 тянет собственную
// копию vite 5, и типы плагинов из vite 6 с ней не сходятся. Этот файл
// намеренно не входит в tsconfig; vitest читает его в первую очередь.
import { defineConfig, mergeConfig } from "vitest/config";

import viteConfig from "./vite.config";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
    },
  }),
);
