// SPDX-License-Identifier: Unlicense
// The front end's one way to the engine: requests answered by id, and runs' and jobs' events. Two transports
// carry the same lines: the Tauri core in the app, the Vite dev server's WebSocket in a plain browser (the demo).
import type { EngineEvent, Message } from "./protocol";

/** Carries one message each way; the engine's lines arrive parsed. */
export interface Transport {
  send(message: object): void;
  listen(receive: (message: Message) => void): void;
}

export class EngineError extends Error {}

export class Client {
  private next = 1;
  private pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();
  private listeners = new Set<(event: EngineEvent) => void>();
  /** Called when the engine's process has ended. */
  onEnded: () => void = () => {};

  constructor(private transport: Transport) {
    transport.listen((message) => this.receive(message));
  }

  /** Send the request NAME and return its result; an error answer rejects with EngineError. */
  request<T>(name: string, fields: Record<string, unknown> = {}): Promise<T> {
    const id = this.next++;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: (value) => resolve(value as T), reject });
      this.transport.send({ id, request: name, ...fields });
    });
  }

  /** Call LISTENER with every run's and job's event; return what stops it. */
  onEvent(listener: (event: EngineEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private receive(message: Message): void {
    if ("event" in message) {
      if (message.event === "engine-ended") this.onEnded();
      else for (const listener of this.listeners) listener(message);
      return;
    }
    const waiting = message.id === null ? undefined : this.pending.get(message.id);
    if (waiting === undefined) return;
    this.pending.delete(message.id as number);
    if ("error" in message) waiting.reject(new EngineError(message.error));
    else waiting.resolve(message.result);
  }
}

const underTauri = (): boolean => "__TAURI_INTERNALS__" in window;

/** The Vite dev server's WebSocket: `pnpm dev` in a plain browser. */
function viteTransport(): Transport {
  const hot = import.meta.hot;
  if (!hot) throw new Error("no engine: open the app, or run `pnpm dev`");
  return {
    send: (message) => hot.send("op:request", message),
    listen: (receive) => hot.on("op:message", receive),
  };
}

/** The Tauri core: engine_send writes a line to the engine, "engine-line" brings each of its lines. */
async function tauriTransport(): Promise<Transport> {
  const { invoke } = await import("@tauri-apps/api/core");
  const { listen } = await import("@tauri-apps/api/event");
  return {
    send: (message) => void invoke("engine_send", { line: JSON.stringify(message) }),
    listen: (receive) => {
      void listen<string>("engine-line", (event) => {
        try {
          receive(JSON.parse(event.payload) as Message);
        } catch {
          console.warn("engine:", event.payload);
        }
      });
    },
  };
}

export async function connect(): Promise<Client> {
  return new Client(underTauri() ? await tauriTransport() : viteTransport());
}

export interface Pick {
  /** A file to write (a save dialog) rather than one to read. */
  save?: boolean;
  /** A folder rather than a file. */
  folder?: boolean;
  title: string;
  /** The suggested file name (save) or the starting path. */
  suggested?: string;
  /** File types: a name and its extensions without dots. */
  filters?: { name: string; extensions: string[] }[];
}

/** Ask the owner for a path: the system's dialog in the app, a prompt in a browser. "" when cancelled. */
export async function pickPath(pick: Pick): Promise<string> {
  if (!underTauri()) return window.prompt(`${pick.title}: a path on this computer`, pick.suggested ?? "") ?? "";
  const dialog = await import("@tauri-apps/plugin-dialog");
  const options = { title: pick.title, defaultPath: pick.suggested, filters: pick.filters };
  const found = pick.save
    ? await dialog.save(options)
    : await dialog.open({ ...options, directory: Boolean(pick.folder), multiple: false });
  return typeof found === "string" ? found : "";
}

/** Call ASK when the owner closes the app's window (nothing in a browser); it answers with closeWindow or keepWindow. */
export async function onCloseRequested(ask: () => void): Promise<void> {
  if (!underTauri()) return;
  const { listen } = await import("@tauri-apps/api/event");
  await listen("close-requested", ask);
}

/** Close the app's window: nothing runs, or the owner chose to stop it. */
export async function closeWindow(): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("close_window");
}

/** Keep the app's window open: the next close asks again. */
export async function keepWindow(): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("keep_window");
}
