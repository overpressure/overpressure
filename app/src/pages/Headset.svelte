<!-- SPDX-License-Identifier: Unlicense -->
<!-- A headset: a compact hero (its picture, its name, one line of facts; an update and Change version on the right),
     then the headset itself. A headset that looks at itself on USB asks to be plugged in first; the look changes
     nothing and finds what runs on it, and the actions offered are those that fit that state, the recommended one
     first. Then the tools as tiles: the backup, the logs, Advanced mode. The long words live in tooltips. -->
<script lang="ts">
  import { app } from "../lib/app.svelte";
  import { pickPath } from "../lib/bridge";
  import type { JobState } from "../lib/job";
  import type { Found, HeadsetEntry, Workflow } from "../lib/protocol";
  import { channelName, shortSoc, shortVersion } from "../lib/words";
  import AddDialog from "../components/AddDialog.svelte";
  import BackupDialog from "../components/BackupDialog.svelte";
  import Badge from "../components/Badge.svelte";
  import HeadsetArt from "../components/HeadsetArt.svelte";
  import Icon, { type IconName } from "../components/Icon.svelte";
  import JobStatus from "../components/JobStatus.svelte";

  let { headset }: { headset: HeadsetEntry } = $props();

  const ICONS: Record<string, IconName> = { install: "download", upgrade: "up", restore: "restore", check: "shield" };
  const STATES: Record<string, { icon: IconName; tone: string; words: (f: Found) => string }> = {
    maker: { icon: "cpu", tone: "info", words: (f) => [f.system || "Its own system", f.version].filter(Boolean).join(" ") },
    overpressure: { icon: "check", tone: "good", words: (f) => ["overpressure", f.version].filter(Boolean).join(" ") },
    bootloader: { icon: "terminal", tone: "warn", words: () => "In its bootloader" },
    recovery: { icon: "restore", tone: "warn", words: () => "In its recovery" },
    edl: { icon: "alert", tone: "warn", words: () => "In emergency download mode" },
    unknown: { icon: "alert", tone: "bad", words: () => "Connected, but not readable" },
  };

  let changing = $state(false);
  let backingUp = $state(false);
  let skipped = $state(false);
  let looking = $state(false);
  let job = $state<JobState | null>(null);

  const busy = $derived(app.running || app.jobRunning);
  const update = $derived(headset.checkout ? "" : app.updates[headset.codename]);
  const found = $derived(app.looks[headset.codename]);
  const present = $derived(Boolean(found && found.state && found.state !== "absent"));
  const waiting = $derived(headset.assesses && !present && !skipped);
  const kind = $derived(found?.state ? STATES[found.state] : undefined);
  const offered = $derived.by((): Workflow[] => {
    if (!headset.assesses || !present || !found?.workflows.length) return headset.workflows;
    return found.workflows.flatMap((key) => headset.workflows.filter((w) => w.key === key));
  });
  const recommended = $derived(present && found?.workflows.length ? offered[0]?.key : "");

  // Look at the headset while the page is open: every 1.5 s until it is there, then every 5 s (it may be
  // unplugged). Never while a run or a job has the headset's USB.
  $effect(() => {
    const codename = headset.codename;
    if (!headset.assesses) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async (): Promise<void> => {
      if (!app.running && !app.jobRunning) {
        looking = true;
        await app.look(codename, !(codename in app.looks));
        looking = false;
      }
      const there = app.looks[codename]?.state;
      if (!stopped) timer = setTimeout(tick, there && there !== "absent" ? 5000 : 1500);
    };
    timer = setTimeout(tick, 0); // outside the effect: what tick reads is no dependency
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  });

  async function lookAgain(): Promise<void> {
    looking = true;
    await app.look(headset.codename);
    looking = false;
  }

  function blocked(w: Workflow): string {
    if (w.problem) return w.problem;
    if (app.running) return "A run is under way.";
    if (app.jobRunning) return "Something else is running. Let it finish first.";
    return "";
  }

  function press(w: Workflow): void {
    const reason = blocked(w);
    if (reason) app.say(reason);
    else void app.choose(w.key);
  }

  async function startJob(what: "download" | "logs", request: string, fields: Record<string, unknown>): Promise<void> {
    try {
      job = await app.startJob(what, request, { codename: headset.codename, ...fields });
    } catch (error) {
      app.say(error instanceof Error ? error.message : String(error));
    }
  }

  function updateNow(): void {
    void startJob("download", "download", { channel: headset.channel || "stable", version: "" });
  }

  async function saveLogs(): Promise<void> {
    const path = await pickPath({
      save: true,
      title: "Save the logs",
      suggested: `overpressure-logs-${headset.codename}.zip`,
      filters: [{ name: "Zip", extensions: ["zip"] }],
    });
    if (path) await startJob("logs", "save_logs", { path });
  }
</script>

<section class="hero">
  <HeadsetArt codename={headset.codename} name={headset.name} size={72} />
  <div class="info">
    <div class="name">
      <h2 class="title">{headset.name}</h2>
      {#if headset.checkout}<Badge icon="branch" tone="info" tip="Development checkout: updated with git, never replaced" />{/if}
      {#if headset.pinned}<Badge icon="pin" tip="Kept at this release: updates leave it" />{/if}
    </div>
    <p class="facts muted">
      <span data-tip={headset.soc || undefined}>{shortSoc(headset.soc)}</span>
      {#if headset.year}<span>{headset.year}</span>{/if}
      <span data-tip={headset.downloaded || undefined}
        >{shortVersion(headset.downloaded)}{headset.channel ? ` (${channelName(headset.channel)})` : ""}</span
      >
    </p>
  </div>
  <div class="hero-actions">
    {#if update}
      <button
        class="btn update"
        data-tip="Download the support for {update}: the headset follows when you upgrade it"
        disabled={busy}
        onclick={updateNow}><Icon name="up" /> Update to {update}</button
      >
    {/if}
    {#if !headset.checkout}
      <button class="btn secondary" disabled={busy} onclick={() => (changing = true)}>Change version</button>
    {/if}
  </div>
</section>
{#if job}<div class="job"><JobStatus {job} /></div>{/if}

{#if waiting}
  <section class="connect" aria-live="polite">
    <div class="plug"><Icon name="plug" size={30} /></div>
    <h2>Connect your {headset.name}</h2>
    <p class="muted">
      Switch it on and connect it to this computer with a USB-C cable. overpressure looks at what runs on it, without
      changing anything, and offers what fits.
    </p>
    {#if found?.note}<p class="callout info note"><Icon name="info" />{found.note}</p>{/if}
    <p class="looking muted"><span class="spinner" aria-hidden="true"></span>Looking for it…</p>
    <button
      class="btn ghost skip"
      data-tip="Offer every action without looking at the headset first"
      onclick={() => (skipped = true)}>Show every action</button
    >
  </section>
{:else}
  {#if headset.assesses && present && found && kind}
    <section class="found {kind.tone}">
      <span class="state-icon"><Icon name={kind.icon} /></span>
      <div class="found-text">
        <b>{kind.words(found)}</b>
        {#if found.note}<small>{found.note}</small>{/if}
      </div>
      <div class="facts-pills">
        {#each found.facts as [label, value] (label)}<span class="pill">{label}: {value}</span>{/each}
      </div>
      <button
        class="iconbtn"
        data-tip="Look again"
        aria-label="Look at the headset again"
        disabled={looking || busy}
        onclick={lookAgain}><Icon name="refresh" /></button
      >
    </section>
  {/if}

  <h2 class="heading">Actions</h2>
  <div class="cards">
    {#each offered as w (w.key)}
      {@const reason = blocked(w)}
      <div class="cell">
        <button
          class="tile"
          class:off={Boolean(reason)}
          class:recommended={w.key === recommended}
          aria-disabled={Boolean(reason)}
          aria-describedby="about-{w.key}"
          data-tip={w.summary}
          onclick={() => press(w)}
        >
          <span class="icon-box"><Icon name={ICONS[w.key] ?? "right"} size={22} /></span>
          <h3>{w.title}</h3>
          <span class="sr-only" id="about-{w.key}"
            >{w.key === recommended ? "Recommended. " : ""}{w.summary} {reason}</span
          >
        </button>
        <span class="badges">
          {#if w.key === recommended}<Badge icon="star" tone="info" tip="Recommended for the headset as it is now" />{/if}
          {#if w.problem}<Badge icon="lock" tip={w.problem} />{/if}
          {#if w.erases}<Badge icon="alert" tone="warn" tip="Erases the headset: its apps, games and data" />{/if}
        </span>
      </div>
    {:else}
      <p class="muted">No installer yet: Advanced mode has its steps.</p>
    {/each}
  </div>
{/if}

<h2 class="heading">Tools</h2>
<div class="cards">
  <button
    class="tile"
    data-tip="The headset's backup on this computer: export it, or import one"
    onclick={() => (backingUp = true)}
  >
    <span class="icon-box"><Icon name="archive" size={22} /></span>
    <h3>Backup</h3>
  </button>
  <button
    class="tile"
    class:off={busy}
    aria-disabled={busy}
    data-tip="The logs for a bug report in one file, this computer's identifiers masked"
    onclick={() => (busy ? app.say("Something is running. Let it finish first.") : void saveLogs())}
  >
    <span class="icon-box"><Icon name="file" size={22} /></span>
    <h3>Save logs</h3>
  </button>
  {#if headset.has_steps}
    <button class="tile" data-tip="Every step on its own, and the dependency checks" onclick={() => app.openAdvanced()}>
      <span class="icon-box"><Icon name="sliders" size={22} /></span>
      <h3>Advanced</h3>
    </button>
  {/if}
</div>

{#if changing}<AddDialog codename={headset.codename} onclose={() => (changing = false)} />{/if}
{#if backingUp}<BackupDialog {headset} onclose={() => (backingUp = false)} />{/if}

<style>
  .hero {
    display: flex;
    gap: 16px;
    align-items: center;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--line);
  }

  .info {
    flex: 1;
    min-width: 0;
  }

  .name {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .title {
    font-size: 22px;
    font-weight: 650;
    letter-spacing: -0.4px;
    margin-right: 4px;
  }

  .facts {
    display: flex;
    flex-wrap: wrap;
    margin-top: 3px;
    font-size: 12px;
  }

  .facts span + span::before {
    content: "·";
    margin: 0 6px;
  }

  .hero-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
  }

  .btn.update {
    background: var(--update-bg);
    border-color: var(--update-text);
    color: var(--update-text);
  }

  .btn.update:hover:not(:disabled) {
    filter: brightness(1.08);
  }

  .job {
    max-width: 420px;
    margin-top: 12px;
  }

  .connect {
    margin-top: 28px;
    display: grid;
    justify-items: center;
    gap: 10px;
    text-align: center;
    padding: 32px 24px;
    border: 1px dashed var(--line);
    border-radius: 14px;
  }

  .connect > p {
    max-width: 440px;
    font-size: 13px;
    line-height: 1.5;
  }

  .plug {
    width: 64px;
    height: 64px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    background: var(--blue-bg);
    color: var(--blue);
    margin-bottom: 4px;
  }

  .note {
    display: flex;
    gap: 8px;
    align-items: center;
    text-align: left;
  }

  .looking {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
  }

  .spinner {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid var(--track);
    border-top-color: var(--blue);
    animation: spin 0.9s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .skip {
    font-size: 12px;
    min-height: 30px;
  }

  .found {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 16px;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid var(--line);
    background: var(--panel);
  }

  .state-icon {
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    border-radius: 50%;
    display: grid;
    place-items: center;
    background: var(--pill-bg);
  }

  .found.info .state-icon {
    background: var(--blue-bg);
    color: var(--blue-text);
  }

  .found.good .state-icon {
    background: var(--good-bg);
    color: var(--good-text);
  }

  .found.warn .state-icon {
    background: var(--update-bg);
    color: var(--update-text);
  }

  .found.bad .state-icon {
    background: var(--bad-bg);
    color: var(--bad-text);
  }

  .found-text {
    display: grid;
    min-width: 0;
  }

  .found-text small {
    font-size: 12px;
    color: var(--muted);
  }

  .facts-pills {
    flex: 1;
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
  }

  .heading {
    margin: 24px 0 12px;
    font-size: 14px;
  }

  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(124px, 140px));
    gap: 12px;
  }

  .cell {
    position: relative;
    display: grid;
  }

  .tile {
    aspect-ratio: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 12px;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: var(--panel);
    transition:
      background 0.12s,
      border-color 0.12s;
  }

  .tile h3 {
    font-size: 13px;
  }

  .tile:hover:not(.off) {
    background: var(--hover);
    border-color: var(--blue);
  }

  .tile:hover:not(.off) .icon-box {
    color: var(--blue);
  }

  .tile.recommended {
    border-color: var(--blue);
    background: var(--selected);
  }

  .tile.off {
    cursor: not-allowed;
  }

  .tile.off > :not(.sr-only) {
    opacity: 0.45;
  }

  .icon-box {
    width: 42px;
    height: 42px;
    border-radius: 11px;
    background: var(--surface);
    display: grid;
    place-items: center;
  }

  .badges {
    position: absolute;
    top: 8px;
    right: 8px;
    display: flex;
    gap: 4px;
  }

  @media (max-width: 680px) {
    .hero {
      flex-wrap: wrap;
    }

    .hero-actions {
      width: 100%;
      justify-content: flex-start;
    }

    .found {
      flex-wrap: wrap;
    }

    .cards {
      grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
    }
  }
</style>
