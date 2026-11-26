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
  year: number;
  repository: string;
  /** In development: listed, nothing to download yet. */
  development: boolean;
  /** What is downloaded, in words ("release 2026.11.0b0", "development checkout"), or "". */
  downloaded: string;
  /** A Git clone: updated with git, never replaced by a download. */
  checkout: boolean;
  channel: string;
  pinned: boolean;
  refused: string;
  workflows: Workflow[];
  has_steps: boolean;
}

export interface Release {
  version: string;
  prerelease: boolean;
  /** Why this overpressure cannot run it, or "". */
  problem: string;
}

export interface Releases {
  releases: Release[];
  record: { tag?: string | null; channel?: string | null; pinned?: boolean | null };
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

/** A plan as an earlier run left it (the plan request). */
export interface RecordedStage extends PlanStage {
  tasks: (PlanTask & { status: TaskStatus })[];
}

export interface FinishedPage {
  title: string;
  text: string;
  actions: { label: string; workflow: string; step: string }[];
}

export interface Backup {
  supported: boolean;
  present: boolean;
  folder: string;
}

export interface Param {
  name: string;
  label: string;
  kind: "text" | "path" | "choice" | "flag";
  choices: string[];
  help: string;
  value: string;
  /** What Browse picks: "file", "folder", or "" for none. */
  browse: "file" | "folder" | "";
}

export interface StepInfo {
  key: string;
  title: string;
  /** The dependency checks rather than a step. */
  checks: boolean;
  help?: string;
  writes?: boolean;
  linux_root?: boolean;
  params?: Param[];
}

export interface StepGroup {
  group: string;
  steps: StepInfo[];
}

export interface Check {
  name: string;
  needed_for: string;
  status: "ok" | "missing" | "optional" | "warning";
  detail: string;
  fix: string;
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

export type JobKind = "download" | "export" | "import" | "logs" | "step";

/** A job's event: a download, the backup's export or import, the logs, a step. */
export type JobEvent = { job: string } & (
  | { event: "job-line"; line: string }
  | { event: "job-progress"; fraction: number | null; text: string }
  | { event: "job-ended"; kind: JobKind; code: number; error: string }
);

/** The engine's process has ended (the app's core or the dev bridge says so): the app has to start again. */
export type EngineEnded = { event: "engine-ended"; run: "" };

export type Answer = { id: number | null; result: unknown } | { id: number | null; error: string };
export type EngineEvent = RunEvent | JobEvent;
export type Message = Answer | EngineEvent | EngineEnded;
