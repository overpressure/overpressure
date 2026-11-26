<!-- SPDX-License-Identifier: Unlicense -->
<!-- Advanced mode: every step of the headset on its own. The list on the left (the dependency checks first);
     on the right the step's parameters, the command line it runs (to copy into a terminal), Run or Stop, and
     its output. A step that writes to the headset asks first. One thing at a time, as everywhere. -->
<script lang="ts">
  import { tick } from "svelte";
  import { app } from "../lib/app.svelte";
  import { pickPath } from "../lib/bridge";
  import type { JobState } from "../lib/job";
  import type { Check, HeadsetEntry, StepGroup, StepInfo } from "../lib/protocol";
  import Icon from "../components/Icon.svelte";
  import JobStatus from "../components/JobStatus.svelte";

  let { headset }: { headset: HeadsetEntry } = $props();

  const STATUS: Record<Check["status"], { word: string; tone: string }> = {
    ok: { word: "OK", tone: "good" },
    missing: { word: "Missing", tone: "bad" },
    optional: { word: "Optional", tone: "" },
    warning: { word: "Warning", tone: "warn" },
  };

  let groups = $state<StepGroup[]>([]);
  let selected = $state(app.step || "doctor");
  let values = $state<Record<string, string>>({});
  let command = $state("");
  let problem = $state("");
  let jobs = $state<Record<string, JobState>>({});
  let checks = $state<Check[] | null>(null);
  let checking = $state(false);
  let confirm = $state<HTMLDialogElement>();
  let output = $state<HTMLPreElement>();
  let timer: ReturnType<typeof setTimeout> | undefined;

  const step = $derived(groups.flatMap((g) => g.steps).find((s) => s.key === selected));
  const job = $derived(jobs[selected]);
  const busy = $derived(app.running || app.jobRunning);
  const missing = $derived(checks?.filter((c) => c.status === "missing").length ?? 0);
  const byNeed = $derived.by(() => {
    const found = new Map<string, Check[]>();
    for (const check of checks ?? []) found.set(check.needed_for, [...(found.get(check.needed_for) ?? []), check]);
    return [...found.entries()];
  });

  $effect(() => {
    const codename = headset.codename;
    void app.engine.request<{ groups: StepGroup[] }>("steps", { codename }).then((found) => {
      groups = found.groups;
      if (!groups.some((g) => g.steps.some((s) => s.key === selected))) selected = "doctor";
      select(selected);
    });
  });

  function select(key: string): void {
    selected = key;
    app.step = key === "doctor" ? "" : key;
    const found = groups.flatMap((g) => g.steps).find((s) => s.key === key);
    if (found && !found.checks) {
      values = Object.fromEntries((found.params ?? []).map((p) => [p.name, p.value]));
      void refreshCommand();
    } else if (checks === null) void runChecks();
  }

  function changed(): void {
    clearTimeout(timer);
    timer = setTimeout(() => void refreshCommand(), 150);
  }

  async function refreshCommand(): Promise<void> {
    if (!step || step.checks) return;
    try {
      const found = await app.engine.request<{ command: string; problem: string }>("command", {
        codename: headset.codename,
        step: step.key,
        values,
      });
      command = found.command;
      problem = found.problem;
    } catch (error) {
      problem = error instanceof Error ? error.message : String(error);
    }
  }

  async function browse(name: string, kind: "file" | "folder", label: string): Promise<void> {
    const path = await pickPath({ title: label, folder: kind === "folder", suggested: values[name] });
    if (path) {
      values[name] = path;
      changed();
    }
  }

  async function runChecks(): Promise<void> {
    checking = true;
    try {
      checks = (await app.engine.request<{ checks: Check[] }>("checks", { codename: headset.codename })).checks;
    } finally {
      checking = false;
    }
  }

  function press(current: StepInfo): void {
    if (current.writes) confirm?.showModal();
    else void start(current);
  }

  async function start(current: StepInfo): Promise<void> {
    confirm?.close();
    try {
      jobs[current.key] = await app.startJob("step", "run_step", {
        codename: headset.codename,
        step: current.key,
        values,
      });
    } catch (error) {
      app.say(error instanceof Error ? error.message : String(error));
    }
  }

  async function copy(): Promise<void> {
    await navigator.clipboard.writeText(command);
    app.say("The command line is copied.");
  }

  $effect(() => {
    void job?.lines.length;
    if (output) void tick().then(() => output && (output.scrollTop = output.scrollHeight));
  });
</script>

<div class="advanced">
  <nav aria-label="Steps">
    {#each groups as g (g.group)}
      <h3>{g.group}</h3>
      {#each g.steps as s (s.key)}
        <button class:selected={selected === s.key} onclick={() => select(s.key)}>
          {s.checks ? (checks ? `${s.title} (${missing ? `${missing} missing` : "all there"})` : s.title) : s.title}
          {#if jobs[s.key]?.code === null}<span class="dot" title="Running"></span>{/if}
        </button>
      {/each}
    {/each}
  </nav>

  <section class="pane">
    <select class="picker" aria-label="Step" value={selected} onchange={(e) => select(e.currentTarget.value)}>
      {#each groups as g (g.group)}
        <optgroup label={g.group}>
          {#each g.steps as s (s.key)}<option value={s.key}>{s.title}</option>{/each}
        </optgroup>
      {/each}
    </select>
    {#if selected === "doctor" || step?.checks}
      <div class="head">
        <div>
          <h1>Check dependencies</h1>
          <p class="muted">What this computer has and lacks for the {headset.name}. The checks only look; they install nothing.</p>
        </div>
        <button class="btn secondary" disabled={checking} onclick={runChecks}><Icon name="refresh" /> Check again</button>
      </div>
      {#if checking && !checks}
        <p class="muted">Checking…</p>
      {:else}
        {#each byNeed as [need, list] (need)}
          <h3 class="need">{need}</h3>
          <div class="checks">
            {#each list as c (c.name)}
              <div class="check">
                <span class="pill {STATUS[c.status].tone}">{STATUS[c.status].word}</span>
                <div>
                  <b>{c.name}</b>
                  <p class="muted">{c.detail}</p>
                  {#if c.fix && c.status !== "ok"}<p class="fix">{c.fix}</p>{/if}
                </div>
              </div>
            {/each}
          </div>
        {/each}
      {/if}
    {:else if step}
      <div class="head">
        <div>
          <h1>{step.title}</h1>
          <div class="pills">
            {#if step.writes}<span class="pill warn">Writes to the headset</span>{/if}
            {#if step.linux_root}<span class="pill info">In the build container</span>{/if}
          </div>
          {#if step.help}<p class="muted">{step.help}</p>{/if}
        </div>
      </div>

      {#if step.params?.length}
        <div class="params">
          {#each step.params as p (p.name)}
            <label class="param" class:flag={p.kind === "flag"}>
              {#if p.kind === "flag"}
                <input
                  type="checkbox"
                  checked={values[p.name] === "1"}
                  onchange={(e) => {
                    values[p.name] = e.currentTarget.checked ? "1" : "";
                    changed();
                  }}
                />
                <span>{p.label}</span>
              {:else}
                <span>{p.label}</span>
                {#if p.kind === "choice"}
                  <select bind:value={values[p.name]} onchange={changed}>
                    {#each p.choices as choice (choice)}<option value={choice}>{choice || "(none)"}</option>{/each}
                  </select>
                {:else}
                  <span class="field">
                    <input bind:value={values[p.name]} oninput={changed} spellcheck="false" />
                    {#if p.browse}
                      <button class="btn secondary" type="button" onclick={() => browse(p.name, p.browse as "file" | "folder", p.label)}
                        >Browse…</button
                      >
                    {/if}
                  </span>
                {/if}
              {/if}
              {#if p.help}<small class="muted">{p.help}</small>{/if}
            </label>
          {/each}
        </div>
      {/if}

      <div class="command">
        <code>{command}</code>
        <button class="iconbtn" title="Copy the command line" aria-label="Copy the command line" onclick={copy}
          ><Icon name="copy" /></button
        >
      </div>
      {#if problem}<p class="callout bad"><Icon name="alert" /> {problem}</p>{/if}

      <div class="actions">
        {#if job}<div class="status"><JobStatus {job} /></div>{/if}
        {#if job?.code === null}
          <button class="btn secondary" onclick={() => app.engine.request("stop_job", { job: job.id })}
            ><Icon name="stop" /> Stop</button
          >
        {:else}
          <button class="btn primary" disabled={busy || Boolean(problem)} onclick={() => press(step)}>Run</button>
        {/if}
      </div>
      {#if job}
        <pre class="output" bind:this={output}>{job.lines.slice(-400).join("\n")}</pre>
      {/if}

      <dialog bind:this={confirm} aria-labelledby="confirm-title">
        <div class="dtitle"><h2 id="confirm-title">Write to the headset?</h2></div>
        <p class="dbody">“{step.title}” writes to the {headset.name}. Keep it connected until the step has finished.</p>
        <div class="dfoot">
          <button class="btn secondary" onclick={() => confirm?.close()}>Cancel</button>
          <button class="btn primary" onclick={() => start(step)}>Run it</button>
        </div>
      </dialog>
    {/if}
  </section>
</div>

<style>
  .advanced {
    display: grid;
    grid-template-columns: 220px minmax(0, 1fr);
    gap: 24px;
    align-items: start;
  }

  nav {
    display: grid;
    gap: 2px;
    position: sticky;
    top: 0;
  }

  nav h3 {
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    margin: 12px 8px 4px;
  }

  nav h3:first-child {
    margin-top: 0;
  }

  nav button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 7px 10px;
    border-radius: 7px;
    text-align: left;
    font-size: 13px;
  }

  nav button:hover {
    background: var(--hover);
  }

  nav button.selected {
    background: var(--selected);
    color: var(--text);
    font-weight: 600;
  }

  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--blue);
  }

  .pane {
    display: grid;
    gap: 16px;
    min-width: 0;
  }

  .head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
  }

  .head h1 {
    font-size: 22px;
  }

  .head p {
    margin-top: 6px;
    font-size: 12px;
  }

  .pills {
    display: flex;
    gap: 6px;
    margin-top: 6px;
  }

  .pills:empty {
    display: none;
  }

  .need {
    font-size: 12px;
    color: var(--muted);
    font-weight: 600;
    margin-bottom: -8px;
  }

  .checks {
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--panel);
  }

  .check {
    display: grid;
    grid-template-columns: 76px minmax(0, 1fr);
    align-items: start;
    gap: 10px;
    padding: 10px 14px;
  }

  .check + .check {
    border-top: 1px solid var(--line);
  }

  .check .pill {
    justify-self: start;
    margin-top: 1px;
  }

  .check p {
    font-size: 12px;
  }

  .fix {
    color: var(--blue);
    margin-top: 2px;
  }

  .params {
    display: grid;
    gap: 12px;
  }

  .param {
    display: grid;
    gap: 5px;
    font-size: 12px;
  }

  .param.flag {
    grid-template-columns: auto 1fr;
    align-items: center;
    gap: 8px;
  }

  .param.flag small {
    grid-column: 2;
  }

  .field {
    display: flex;
    gap: 8px;
  }

  .param input:not([type="checkbox"]),
  .param select {
    flex: 1;
    min-width: 0;
    padding: 7px 9px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: var(--inset);
    color: var(--text);
  }

  .command {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 8px 8px 12px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--inset);
  }

  .command code {
    flex: 1;
    min-width: 0;
    font: 12px/1.5 ui-monospace, "Cascadia Mono", Menlo, monospace;
    overflow-wrap: anywhere;
  }

  .actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;
  }

  .status {
    flex: 1;
    min-width: 0;
  }

  .output {
    margin: 0;
    max-height: 300px;
    overflow: auto;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--inset);
    font: 12px/1.5 ui-monospace, "Cascadia Mono", Menlo, monospace;
    white-space: pre-wrap;
  }

  .dtitle {
    padding: 16px 20px 8px;
  }

  .dbody {
    padding: 0 20px 16px;
  }

  .dfoot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid var(--line);
  }

  .picker {
    display: none;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--inset);
  }

  @media (max-width: 760px) {
    .advanced {
      grid-template-columns: 1fr;
    }

    nav {
      display: none;
    }

    .picker {
      display: block;
    }
  }
</style>
