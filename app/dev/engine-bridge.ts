// SPDX-License-Identifier: Unlicense
// The front end in a plain browser (pnpm dev): Vite starts `python -m overpressure serve --json --demo`
// and passes its lines over the dev server's WebSocket, as the Tauri core does in the app. The demo only:
// nothing in the browser can touch a headset.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

/** The repository root, where `python -m overpressure` runs. */
const REPO = fileURLToPath(new URL("../..", import.meta.url));

export function engineBridge(): Plugin {
  let engine: ChildProcessWithoutNullStreams | undefined;
  return {
    name: "overpressure-engine-bridge",
    apply: "serve",
    configureServer(server) {
      const log = server.config.logger;
      const start = (): ChildProcessWithoutNullStreams => {
        const python = process.env.OVERPRESSURE_PYTHON ?? "python";
        const child = spawn(python, ["-m", "overpressure", "serve", "--json", "--demo"], {
          cwd: REPO,
          env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" },
        });
        createInterface({ input: child.stdout }).on("line", (line) => {
          try {
            server.ws.send("op:message", JSON.parse(line));
          } catch {
            log.warn(`engine: ${line}`);
          }
        });
        child.stderr.on("data", (data: Buffer) => log.warn(`engine: ${String(data).trimEnd()}`));
        child.on("exit", (code) => {
          log.warn(`engine exited (${code}); the next request starts it again`);
          if (engine === child) engine = undefined;
          server.ws.send("op:message", { event: "engine-ended", run: "" });
        });
        return child;
      };
      server.ws.on("op:request", (data: unknown) => {
        engine ??= start();
        engine.stdin.write(`${JSON.stringify(data)}\n`);
      });
      server.httpServer?.on("close", () => engine?.stdin.end());
    },
  };
}
