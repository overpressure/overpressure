<!-- SPDX-License-Identifier: Unlicense -->
<!-- Add a headset: the catalogue, then its version: the newest stable, the newest beta, the preview (main), or
     one release; then the download. A headset in development has only its preview, with a warning. With CODENAME
     it opens at that headset's version (Change version). -->
<script lang="ts">
  import { app } from "../lib/app.svelte";
  import type { JobState } from "../lib/job";
  import type { HeadsetEntry, Releases } from "../lib/protocol";
  import { shortSoc } from "../lib/words";
  import HeadsetArt from "./HeadsetArt.svelte";
  import Icon from "./Icon.svelte";
  import JobStatus from "./JobStatus.svelte";

  let { codename = "", onclose }: { codename?: string; onclose: () => void } = $props();

  type Channel = "stable" | "beta" | "preview" | "version";

  let dialog = $state<HTMLDialogElement>();
  let search = $state("");
  let chosen = $state<HeadsetEntry | undefined>();
  let found = $state<Releases | null>(null);
  let failure = $state("");
  let channel = $state<Channel>("stable");
  let version = $state("");
  let job = $state<JobState | null>(null);
  let understood = $state(false);

  const matches = $derived(
    app.headsets.filter((h) =>
      `${h.name} ${h.codename} ${h.soc}`.toLowerCase().includes(search.trim().toLowerCase()),
    ),
  );
  const runs = $derived((found?.releases ?? []).filter((r) => !r.problem));
  const newestStable = $derived(runs.find((r) => !r.prerelease));
  const newest = $derived(runs[0]);

  $effect(() => {
    dialog?.showModal();
    const first = codename ? app.headsets.find((h) => h.codename === codename) : undefined;
    if (first) void choose(first);
  });

  async function choose(headset: HeadsetEntry): Promise<void> {
    chosen = headset;
    found = null;
    failure = "";
    understood = false;
    try {
      found = await app.engine.request<Releases>("releases", { codename: headset.codename });
      const record = found.record;
      if (found.development) channel = "preview";
      else if (record.pinned && record.tag) {
        channel = "version";
        version = record.tag.replace(/^v/, "");
      } else if (record.channel === "beta" || record.channel === "preview") channel = record.channel;
      else channel = "stable";
      version ||= runs[0]?.version ?? "";
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    }
  }

  async function download(): Promise<void> {
    if (!chosen) return;
    failure = "";
    try {
      job = await app.startJob("download", "download", {
        codename: chosen.codename,
        channel: channel === "version" ? "stable" : channel,
        version: channel === "version" ? version : "",
      });
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    }
  }

  function close(): void {
    dialog?.close();
    onclose();
  }

  $effect(() => {
    if (job?.code === 0 && chosen) {
      const done = chosen.codename;
      setTimeout(() => {
        close();
        app.open(done);
        app.say(`Downloaded the ${chosen?.name ?? done}'s support.`);
      }, 600);
    }
  });
</script>

<dialog bind:this={dialog} aria-labelledby="add-title" onclose={onclose}>
  <div class="title">
    <h2 id="add-title">{chosen ? "Choose a version" : "Add a headset"}</h2>
    <button class="iconbtn" aria-label="Close" onclick={close}><Icon name="close" /></button>
  </div>

  {#if !chosen}
    <label class="search">
      <Icon name="search" />
      <!-- svelte-ignore a11y_autofocus -->
      <input bind:value={search} placeholder="Search headsets…" aria-label="Search headsets" autofocus />
    </label>
    <div class="list">
      {#each matches as h (h.codename)}
        <button class="option" onclick={() => choose(h)}>
          <HeadsetArt codename={h.codename} name={h.name} size={52} />
          <span class="text">
            <b>{h.name}</b>
            <span class="muted" data-tip={h.soc || undefined}>{[shortSoc(h.soc), h.year || ""].filter(Boolean).join(" · ")}</span>
          </span>
          {#if h.downloaded}<span class="pill good">Added</span>
          {:else if h.development}<span class="pill warn">In development</span>{/if}
          <Icon name="right" />
        </button>
      {:else}
        <p class="muted empty">No headset matches.</p>
      {/each}
    </div>
  {:else}
    <div class="body">
      <div class="selected">
        <HeadsetArt codename={chosen.codename} name={chosen.name} size={52} />
        <div>
          <b>{chosen.name}</b>
          <p class="muted">{chosen.downloaded || "Not on this computer yet"}</p>
        </div>
      </div>
      {#if chosen.checkout}
        <p class="callout info">This is a development checkout: update it with git. Downloads never replace it.</p>
      {:else if failure}
        <p class="callout bad">{failure}</p>
      {:else if !found}
        <p class="muted">Looking for its releases…</p>
      {:else if found.development}
        <div class="callout">
          <Icon name="alert" size={20} />
          <div>
            <h3>In development</h3>
            <p>
              Not one release yet: only the newest development state, untested. It may not run at all, and a run
              may leave the headset needing its restore. For testing and development only.
            </p>
          </div>
        </div>
        <label class="channel ack">
          <input type="checkbox" bind:checked={understood} />
          <span><b>I understand</b><small>Download the preview anyway.</small></span>
          <span class="version">main</span>
        </label>
        {#if job}<JobStatus {job} />{/if}
      {:else}
        <fieldset class="channels" disabled={job !== null && job.code === null}>
          <legend class="sr-only">Version</legend>
          <label class="channel">
            <input type="radio" bind:group={channel} value="stable" />
            <span><b>Newest stable</b><small>{newestStable ? "Recommended." : "No stable release yet: the newest beta."}</small></span>
            <span class="version">{newestStable?.version ?? newest?.version ?? ""}</span>
          </label>
          <label class="channel">
            <input type="radio" bind:group={channel} value="beta" />
            <span><b>Newest beta</b><small>Betas too: for testing, and they can break things.</small></span>
            <span class="version">{newest?.version ?? ""}</span>
          </label>
          <label class="channel">
            <input type="radio" bind:group={channel} value="preview" />
            <span><b>Preview</b><small>The newest development state: untested on the headset.</small></span>
            <span class="version">main</span>
          </label>
          <label class="channel">
            <input type="radio" bind:group={channel} value="version" />
            <span
              ><b>One release</b><small>Kept as it is: updates leave it.</small>
              <select bind:value={version} disabled={channel !== "version"} aria-label="Release">
                {#each runs as r (r.version)}<option value={r.version}>{r.version}{r.prerelease ? " (beta)" : ""}</option>{/each}
              </select></span
            >
          </label>
        </fieldset>
        {#if job}<JobStatus {job} />{/if}
      {/if}
    </div>
    <div class="foot">
      {#if !codename}
        <button class="btn ghost" onclick={() => (chosen = undefined)}><Icon name="left" /> Back</button>
      {:else}<span></span>{/if}
      <button
        class="btn primary"
        disabled={!found ||
          chosen.checkout ||
          (found.development && !understood) ||
          (job !== null && job.code === null) ||
          app.running ||
          app.jobRunning}
        onclick={download}><Icon name="download" /> Download</button
      >
    </div>
  {/if}
</dialog>

<style>
  .title {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px 12px;
  }

  .search {
    display: flex;
    align-items: center;
    gap: 9px;
    margin: 0 20px 8px;
    padding: 0 12px;
    height: 38px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--inset);
    color: var(--muted);
  }

  .search:focus-within {
    border-color: var(--blue);
  }

  .search input {
    flex: 1;
    border: 0;
    outline: none;
    background: transparent;
    color: var(--text);
  }

  .list {
    padding: 0 12px 12px;
  }

  .option {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 8px;
    border-radius: 8px;
    border: 1px solid transparent;
    text-align: left;
    color: var(--muted);
  }

  .option:hover:not(:disabled) {
    background: var(--selected);
    border-color: var(--blue);
  }

  .option:disabled {
    opacity: 0.6;
  }

  .text {
    flex: 1;
    display: grid;
    color: var(--text);
  }

  .text .muted {
    font-size: 12px;
  }

  .empty {
    padding: 16px 8px;
  }

  .body {
    padding: 0 20px 16px;
    display: grid;
    gap: 12px;
  }

  .selected {
    display: flex;
    align-items: center;
    gap: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--line);
  }

  .selected p {
    font-size: 12px;
  }

  .channels {
    border: 0;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 6px;
  }

  .channel {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--line);
    cursor: pointer;
  }

  .channel:has(input:checked) {
    border-color: var(--blue);
    background: var(--selected);
  }

  .channel input {
    margin-top: 3px;
    accent-color: var(--blue);
  }

  .channel > span:not(.version) {
    flex: 1;
    display: grid;
    gap: 2px;
  }

  small {
    font-size: 12px;
    color: var(--muted);
  }

  .version {
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
  }

  select {
    margin-top: 6px;
    padding: 5px 8px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: var(--inset);
  }

  .foot {
    display: flex;
    justify-content: space-between;
    padding: 12px 20px;
    border-top: 1px solid var(--line);
  }

  .callout {
    font-size: 12px;
  }

  .callout p {
    margin-top: 2px;
    line-height: 1.5;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
</style>
