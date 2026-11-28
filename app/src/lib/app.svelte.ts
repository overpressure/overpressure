// SPDX-License-Identifier: Unlicense
// What the whole window shares: the engine, the headsets, the page, the run and the jobs. A run or a job keeps
// going while the pages change; events of one whose id is not known yet wait until it is.
import { connect, type Client } from "./bridge";
import { applyJob, newJob, type JobState } from "./job";
import {
  PROTOCOL_MAJOR,
  type Appearance,
  type AppUpdate,
  type EngineEvent,
  type Found,
  type HeadsetEntry,
  type Hello,
  type Earlier,
  type JobKind,
  type PlanAnswer,
  type RecordedStage,
  type Settings,
} from "./protocol";
import { apply, initial, type RunState } from "./run";

export type Page = "loading" | "welcome" | "setup" | "library" | "headset" | "run" | "advanced";

/** A workflow chosen, before it runs: its plan as the last run left it, and how that run ended. */
export interface Ready {
  codename: string;
  workflow: string;
  title: string;
  stages: RecordedStage[];
  earlier: Earlier | null;
}

/** An earlier overpressure's data to bring over: a folder found (from, question), or the app asking for one (ask). */
export interface CarryOffer {
  from: string;
  question: string;
  ask?: boolean;
}

export class AppState {
  client = $state.raw<Client>();
  hello = $state<Hello>();
  settings = $state<Settings>();
  headsets = $state<HeadsetEntry[]>([]);
  /** Newer releases by codename. */
  updates = $state<Record<string, string>>({});
  /** A newer overpressure with the app in it, and whether this app installs it itself (else: its page). */
  appUpdate = $state<{ update: AppUpdate; installs: boolean } | null>(null);
  /** The update's dialog is open. */
  updateOpen = $state(false);
  page = $state<Page>("loading");
  codename = $state("");
  /** The step Advanced mode opens at ("" for the checks). */
  step = $state("");
  run = $state<RunState | null>(null);
  jobs = $state<Record<string, JobState>>({});
  /** The job under way, or the last one, by id. */
  job = $state<string | null>(null);
  /** The workflow the run page shows before it runs. */
  ready = $state<Ready | null>(null);
  /** The last look at each headset on USB, by codename. */
  looks = $state<Record<string, Found>>({});
  problem = $state("");
  /** A short message at the bottom of the window. */
  notice = $state("");
  /** What the app waits for now, in words (the status bar's spinner); "" for nothing. */
  busy = $state("");
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

  /** Look for a newer overpressure; SAY: tell the owner when there is none (they asked). */
  async checkAppUpdate(say = false): Promise<void> {
    if (say) this.busy = "Checking for updates…";
    try {
      const found = await this.engine.request<{ update: AppUpdate | null; installs: boolean }>("app_update");
      this.appUpdate = found.update ? { update: found.update, installs: found.installs } : null;
    } catch {
      this.appUpdate = null; // offline: none known
    } finally {
      if (say) this.busy = "";
    }
    if (say) {
      if (this.appUpdate) this.updateOpen = true;
      else this.say("overpressure is up to date.");
    }
  }

  async accept(everyStart: boolean): Promise<void> {
    this.settings = await this.engine.request<Settings>("accept_disclaimer", { every_start: everyStart });
    this.page = this.hello?.first_setup ? "setup" : "library";
    void this.checkUpdates();
    void this.checkAppUpdate();
  }

  /** The first setup is done (brought over, or started fresh): the headsets, which may have come with it. */
  finishSetup(): void {
    if (this.hello) this.hello.first_setup = false;
    this.page = "library";
    void this.refresh();
    void this.checkUpdates();
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

  /** Look at CODENAME's headset on USB without changing it (FIRST: the page has just opened); null when refused. */
  async look(codename: string, first = false): Promise<Found | null> {
    try {
      const found = await this.engine.request<Found>("assess", { codename, first });
      this.looks[codename] = found;
      return found;
    } catch {
      return null; // a run or a job has the headset: the last look stands
    }
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

  /** Choose WORKFLOW of the open headset. One that changes the headset, or that ran before, opens the run page
   *  ready to start: what it does, how the last run ended, Continue or run a stage again. Another starts at once. */
  async choose(workflow: string): Promise<void> {
    const headset = this.headset;
    if (!headset || this.running || this.jobRunning || this.busy) return;
    const chosen = headset.workflows.find((w) => w.key === workflow);
    const title = chosen?.title ?? workflow;
    this.busy = `Preparing ${title}…`;
    try {
      const { stages, earlier } = await this.engine.request<PlanAnswer>("plan", {
        codename: headset.codename,
        workflow,
      });
      const begun = stages.some((s) => s.tasks.some((t) => t.status !== "pending"));
      if (begun || earlier || chosen?.changes) {
        this.run = null;
        this.ready = { codename: headset.codename, workflow, title, stages, earlier: earlier ?? null };
        this.page = "run";
      } else await this.begin(workflow);
    } catch (error) {
      this.say(error instanceof Error ? error.message : String(error));
    } finally {
      this.busy = "";
    }
  }

  /** Start WORKFLOW of the open headset, from RESTART (a stage) when given. */
  async begin(workflow: string, restart = ""): Promise<void> {
    const headset = this.headset;
    if (!headset) return;
    this.ready = null;
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

  /** Leave the run's page for TO (the headset's page by default): a finished run is closed, a running one goes on. */
  leaveRun(to: "headset" | "library" = "headset"): void {
    if (this.run && !this.running) this.run = null;
    this.ready = null;
    this.page = to === "headset" && this.headset ? "headset" : "library";
  }
}

export const app = new AppState();
