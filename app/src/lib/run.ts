// SPDX-License-Identifier: Unlicense
// A run as its events tell it: the plan's stages and tasks, their states, notes, the pending ask, the log,
// and how far the whole run is. No timers: the ring moves only with what the engine reports.
import type { PlanStage, PlanTask, RunEvent, TaskStatus } from "./protocol";

/** The details view keeps the last lines only. */
export const LOG_LINES = 5000;

export interface TaskState extends PlanTask {
  status: TaskStatus;
  /** The last progress event's fraction; null while unknown. */
  fraction: number | null;
  progress: string;
}

export interface StageState extends Omit<PlanStage, "tasks"> {
  tasks: TaskState[];
}

export type Ask = Extract<RunEvent, { event: "ask" }>;
export type Finished = Extract<RunEvent, { event: "finished" }>;

export interface RunState {
  run: string;
  workflow: string;
  headset: string;
  stages: StageState[];
  notes: { task: string; level: "info" | "warning"; text: string }[];
  ask: Ask | null;
  log: string[];
  finished: Finished | null;
  exited: boolean;
}

export function initial(run: string): RunState {
  return { run, workflow: "", headset: "", stages: [], notes: [], ask: null, log: [], finished: null, exited: false };
}

function findTask(state: RunState, id: string): TaskState | undefined {
  for (const stage of state.stages) {
    const task = stage.tasks.find((t) => t.id === id);
    if (task) return task;
  }
  return undefined;
}

function addLog(state: RunState, line: string): void {
  state.log.push(line);
  if (state.log.length > LOG_LINES) state.log.splice(0, state.log.length - LOG_LINES);
}

/** Apply one of the run's events to STATE (in place); events of another run are ignored. */
export function apply(state: RunState, event: RunEvent): void {
  if (event.run !== state.run) return;
  switch (event.event) {
    case "plan":
      state.workflow = event.workflow;
      state.headset = event.headset.name;
      state.stages = event.stages.map((s) => ({
        ...s,
        tasks: s.tasks.map((t) => ({ ...t, status: "pending", fraction: null, progress: "" })),
      }));
      break;
    case "task": {
      const task = findTask(state, event.task);
      if (task) {
        task.status = event.status;
        if (event.status !== "running") task.fraction = null;
        if (event.status === "running") task.progress = "";
      }
      break;
    }
    case "progress": {
      const task = findTask(state, event.task);
      if (task) {
        task.fraction = event.fraction;
        task.progress = event.text;
      }
      break;
    }
    case "log":
      addLog(state, event.line);
      break;
    case "output":
      addLog(state, event.line);
      break;
    case "note":
      state.notes.push({ task: event.task, level: event.level, text: event.text });
      break;
    case "ask":
      state.ask = event;
      break;
    case "finished":
      state.ask = null;
      state.finished = event;
      break;
    case "exited":
      state.exited = true;
      break;
  }
}

const FINISHED: TaskStatus[] = ["done", "skipped"];

/** A stage's share done, 0 to 1: its tasks weigh the same; a running one counts its reported fraction. */
export function stageFraction(stage: StageState): number {
  if (stage.tasks.length === 0) return 0;
  let done = 0;
  for (const task of stage.tasks) {
    if (FINISHED.includes(task.status)) done += 1;
    else if (task.status === "running" && task.fraction !== null) done += Math.min(Math.max(task.fraction, 0), 1);
  }
  return done / stage.tasks.length;
}

export type StageStatus = "pending" | "active" | "done" | "failed";

export function stageStatus(stage: StageState): StageStatus {
  const states = stage.tasks.map((t) => t.status);
  if (states.includes("failed")) return "failed";
  if (states.length > 0 && states.every((s) => FINISHED.includes(s))) return "done";
  if (states.some((s) => s !== "pending")) return "active";
  return "pending";
}

export interface Overall {
  /** The whole run's share done, 0 to 1: each stage an equal part of the ring. */
  fraction: number;
  /** The stage the run is in: the first not done (the last when all are). */
  stage: number;
  task: TaskState | undefined;
}

export function overall(state: RunState): Overall {
  const count = state.stages.length;
  if (count === 0) return { fraction: 0, stage: 0, task: undefined };
  const fraction = state.stages.reduce((sum, s) => sum + stageFraction(s), 0) / count;
  let stage = state.stages.findIndex((s) => stageStatus(s) !== "done");
  if (stage < 0) stage = count - 1;
  const tasks = state.stages[stage]?.tasks ?? [];
  const task =
    tasks.find((t) => t.status === "running" || t.status === "failed") ??
    tasks.find((t) => !FINISHED.includes(t.status));
  return { fraction, stage, task };
}
