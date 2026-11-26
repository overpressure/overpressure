// SPDX-License-Identifier: Unlicense
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";
import { engineBridge } from "./dev/engine-bridge.ts";

// Under `tauri dev` the Rust core runs the engine; in a plain browser the dev bridge does (the demo only).
const underTauri = Boolean(process.env.TAURI_ENV_PLATFORM);

export default defineConfig({
  plugins: [svelte(), ...(underTauri ? [] : [engineBridge()])],
  clearScreen: false,
  envPrefix: ["VITE_", "TAURI_ENV_"],
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    fs: { allow: [".", "../docs/logo"] },
  },
  build: { target: "es2022", outDir: "dist" },
  test: { include: ["src/**/*.test.ts"] },
});
