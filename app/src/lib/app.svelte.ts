// SPDX-License-Identifier: Unlicense
// What the whole window shares: the engine, the headsets, the page, the run and the jobs. A run or a job keeps
// going while the pages change; events of one whose id is not known yet wait until it is.
import { connect, type Client } from "./bridge";
import { applyJob, newJob, type JobState } from "./job";
import {
  PROTOCOL_MAJOR,
  type Appearance,
  type EngineEvent,
  type HeadsetEntry,
  type Hello,
  type JobKind,
  type RecordedStage,
  type Settings,
} from "./protocol";
import { apply, initial, type RunState } from "./run";

export type Page = "loading" | "welcome" | "library" | "headset" | "run" | "advanced";

/** A workflow chosen after an earlier run of it: continue, or start over from a stage. */
export interface Sheet {
  codename: string;
  workflow: string;
  title: string;
  stages: RecordedStage[];
}

export class AppState {
  client = $state.raw<Client>();
  hello = $state<Hello>();
  settings = $state<Settings>();
  headsets = $state<HeadsetEntry[]>([]);
  /** Newer releases by codename. */
  updates = $state<Record<string, string>>({});
  page = $state<Page>("loading");
  codename = $state("");
  /** The step Advanced mode opens at ("" for the checks). */
  step = $state("");
  run = $state<RunState | null>(null);
  jobs = $state<Record<string, JobState>>({});
  /** The job under way, or the last one, by id. */
  job = $state<string | null>(null);
  sheet = $state<Sheet | null>(null);
  carryOver = $state<{ from: string; question: string } | null>(null);
  problem = $state("");
  /** A short message at the bottom of the window. */
  notice = $state("");
  private early: EngineEvent[] = [];
  private noticeTimer: ReturnType<typeof setTimeout> | undefined;

  headset = $derived(this.headsets.find((h) => h.codename === this.codename));
  running = $derived(this.run !== null && this.run.finished === null);
  jobRunning = $derived(this.job !== null && this.jobs[this.job]?.code === null);

  async start(): Promise<void> {
    try {
      const client = await connect();
      this.client = client;
      client.onEnded = () => (this.problem = "The engine has ended. Close overpressure and start it again.");
      client.onEvent((event) => this.receive(event));
      const hello = await client.request<Hello>("hello");
      this.hello = hello;
      if (Number(hello.protocol.split(".")[0]) > PROTOCOL_MAJOR) {
        this.problem = `This app is older than its engine (protocol ${hello.protocol}): update the app.`;
        return;
      }
      this.settings = await client.request<Settings>("settings");
      await this.refresh();
      if (hello.disclaimer.due) this.page = "welcome";
      else await this.accept(this.settings.disclaimer_every_start);
    } catch (error) {
      this.problem = error instanceof Error ? error.message : String(error);
    }
  }

  private receive(event: EngineEvent): void {
    if ("run" in event) {
      if (this.run && event.run === this.run.run) apply(this.run, event);
      else this.early.push(event);
      if (event.event === "exited") void this.refresh(); // a run changes what the workflows can do
      return;
    }
    const job = this.jobs[event.job];
    if (job) applyJob(job, event);
    else this.early.push(event);
    if (event.event === "job-ended") {
      void this.refresh();
      if (event.kind === "download") void this.checkUpdates();
    }
  }

  /** The engine's client: there once start() has connected. */
  get engine(): Client {
    if (!this.client) throw new Error("not connected");
    return this.client;
  }

  async refresh(): Promise<void> {
    this.headsets = (await this.engine.request<{ headsets: HeadsetEntry[] }>("headsets")).headsets;
  }

  async checkUpdates(): Promise<void> {
    try {
      this.updates = (await this.engine.request<{ updates: Record<string, string> }>("updates")).updates;
    } catch {
      this.updates = {}; // offline: none known
    }
  }

  async accept(everyStart: boolean): Promise<void> {
    this.settings = await this.engine.request<Settings>("accept_disclaimer", { every_start: everyStart });
    this.page = "library";
    void this.checkUpdates();
    const offer = await this.engine.request<{ from: string; question: string }>("carry_over");
    if (offer.from) this.carryOver = offer;
  }

  async setAppearance(appearance: Appearance): Promise<void> {
    this.settings = await this.engine.request<Settings>("set_settings", { appearance });
  }

  say(text: string): void {
    this.notice = text;
    clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => (this.notice = ""), 4000);
  }

  open(codename: string): void {
    this.codename = codename;
    this.page = "headset";
  }

  openAdvanced(step = ""): void {
    this.step = step;
    this.page = "advanced";
  }

  /** Start a job with REQUEST; return its state, which its events keep up to date. */
  async startJob(kind: JobKind, request: string, fields: Record<string, unknown>): Promise<JobState> {
    const { job: id } = await this.engine.request<{ job: string }>(request, fields);
    const job = newJob(id, kind, String(fields.codename ?? ""));
    for (const event of this.early.splice(0)) {
      if ("job" in event) applyJob(job, event);
    }
    this.jobs[id] = job;
    this.job = id;
    return this.jobs[id] as JobState;
  }

  /** Choose WORKFLOW of the open headset: after an earlier run of it, ask first (continue or start over). */
  async choose(workflow: string): Promise<void> {
    const headset = this.headset;
    if (!headset || this.running || this.jobRunning) return;
    try {
      const { stages } = await this.engine.request<{ stages: RecordedStage[] }>("plan", {
        codename: headset.codename,
        workflow,
      });
      const begun = stages.some((s) => s.tasks.some((t) => t.status === "done" || t.status === "skipped"));
      const title = headset.workflows.find((w) => w.key === workflow)?.title ?? workflow;
      if (begun) this.sheet = { codename: headset.codename, workflow, title, stages };
      else await this.begin(workflow);
    } catch (error) {
      this.say(error instanceof Error ? error.message : String(error));
    }
  }

  /** Start WORKFLOW of the open headset, from RESTART (a stage) when given. */
  async begin(workflow: string, restart = ""): Promise<void> {
    const headset = this.headset;
    if (!headset) return;
    this.sheet = null;
    try {
      const { run: id } = await this.engine.request<{ run: string }>("start", {
        codename: headset.codename,
        workflow,
        ...(restart ? { restart } : {}),
      });
      const state = initial(id);
      for (const event of this.early.splice(0)) {
        if ("run" in event) apply(state, event);
      }
      this.run = state;
      this.page = "run";
    } catch (error) {
      this.say(error instanceof Error ? error.message : String(error));
    }
  }

  leaveRun(): void {
    if (this.run && !this.running) this.run = null; // a finished run is closed; a running one keeps going
    this.page = this.headset ? "headset" : "library";
  }
}

export const app = new AppState();
