// SPDX-License-Identifier: Unlicense
// The engine's messages (python -m overpressure serve --json; docs/tool.md, "The app's server").

/** The protocol's major version this front end speaks: a newer major is refused. */
export const PROTOCOL_MAJOR = 0;

export type Appearance = "system" | "light" | "dark";

export interface Hello {
  protocol: string;
  version: string;
  platform: string;
  demo: boolean;
  disclaimer: { title: string; text: string; accept: string; due: boolean; accepted: boolean };
}

export interface Settings {
  appearance: Appearance;
  disclaimer_every_start: boolean;
  disclaimer_seen: string;
}

export interface Workflow {
  key: string;
  title: string;
  summary: string;
  erases: boolean;
  changes: boolean;
  /** Why it cannot run now, or "". */
  problem: string;
}

export interface HeadsetEntry {
  codename: string;
  name: string;
  soc: string;
  repository: string;
  /** What is downloaded, in words ("release 2026.11.0b0", "development checkout"), or "". */
  downloaded: string;
  channel: string;
  pinned: boolean;
  refused: string;
  workflows: Workflow[];
}

export type TaskStatus = "pending" | "running" | "done" | "skipped" | "failed";

export interface PlanTask {
  id: string;
  title: string;
  estimate: string;
  seconds: number | null;
  writes: boolean;
}

export interface PlanStage {
  id: string;
  title: string;
  summary: string;
  tasks: PlanTask[];
}

export interface AskField {
  name: string;
  label: string;
  secret: boolean;
  optional: boolean;
}

/** A run's event: install --json's events with the run's id, then exited. */
export type RunEvent = { run: string } & (
  | { event: "plan"; headset: { codename: string; name: string }; workflow: string; stages: PlanStage[] }
  | { event: "task"; stage: string; task: string; status: TaskStatus }
  | { event: "progress"; task: string; fraction: number | null; text: string }
  | { event: "log"; task: string; line: string }
  | { event: "note"; task: string; level: "info" | "warning"; text: string }
  | {
      event: "ask";
      id: string;
      kind: "confirm" | "action" | "form";
      title: string;
      text: string;
      fields?: AskField[];
      accept: string;
      decline: string;
    }
  | {
      event: "finished";
      status: "done" | "failed" | "stopped";
      task: string | null;
      error?: string;
      where?: string;
      offer?: string;
    }
  | { event: "output"; line: string }
  | { event: "exited"; code: number }
);

export type Answer = { id: number | null; result: unknown } | { id: number | null; error: string };
/** The engine's process has ended (the app's core or the dev bridge says so): the app has to start again. */
export type EngineEnded = { event: "engine-ended"; run: "" };
export type Message = Answer | RunEvent | EngineEnded;
