<!-- SPDX-License-Identifier: Unlicense -->
<!-- The window: the top bar, the breadcrumb on inner pages, the page, and the status bar. A run keeps going
     while the pages change: only Stop stops it, and the status bar leads back to it. -->
<script lang="ts">
  import { onMount } from "svelte";
  import logo from "../../docs/logo/overpressure.svg?url";
  import { connect, type Client } from "./lib/bridge";
  import { PROTOCOL_MAJOR, type Appearance, type HeadsetEntry, type Hello, type RunEvent, type Settings } from "./lib/protocol";
  import { apply, initial, overall, type RunState } from "./lib/run";
  import Icon from "./components/Icon.svelte";
  import Welcome from "./pages/Welcome.svelte";
  import Library from "./pages/Library.svelte";
  import Headset from "./pages/Headset.svelte";
  import Run from "./pages/Run.svelte";

  type Page = "loading" | "welcome" | "library" | "headset" | "run";

  let client = $state.raw<Client>();
  let hello = $state<Hello>();
  let settings = $state<Settings>();
  let headsets = $state<HeadsetEntry[]>([]);
  let page = $state<Page>("loading");
  let codename = $state("");
  let run = $state<RunState | null>(null);
  let problem = $state("");
  let menuOpen = $state(false);
  // Events of a run whose id has not been set yet (its answer and its first events can cross).
  const early: RunEvent[] = [];

  const headset = $derived(headsets.find((h) => h.codename === codename));
  const running = $derived(run !== null && run.finished === null);
  const progress = $derived(run ? Math.round(overall(run).fraction * 100) : 0);

  const systemDark = matchMedia("(prefers-color-scheme: dark)");
  function paint(appearance: Appearance): void {
    document.documentElement.dataset.theme =
      appearance === "system" ? (systemDark.matches ? "dark" : "light") : appearance;
  }
  systemDark.addEventListener("change", () => paint(settings?.appearance ?? "system"));
  paint("system");

  async function refresh(): Promise<void> {
    if (!client) return;
    headsets = (await client.request<{ headsets: HeadsetEntry[] }>("headsets")).headsets;
  }

  onMount(async () => {
    try {
      client = await connect();
      client.onEnded = () => (problem = "The engine has ended. Close overpressure and start it again.");
      client.onEvent((event) => {
        if (run && event.run === run.run) apply(run, event);
        else early.push(event);
        if (event.event === "exited") void refresh(); // a run changes what the workflows can do
      });
      hello = await client.request<Hello>("hello");
      const major = Number(hello.protocol.split(".")[0]);
      if (major > PROTOCOL_MAJOR) {
        problem = `This app is older than its engine (protocol ${hello.protocol}): update the app.`;
        return;
      }
      settings = await client.request<Settings>("settings");
      paint(settings.appearance);
      await refresh();
      if (hello.disclaimer.due) page = "welcome";
      else {
        // Not due on this start: accepted as before, for this engine and what it starts.
        settings = await client.request<Settings>("accept_disclaimer", { every_start: settings.disclaimer_every_start });
        page = "library";
      }
    } catch (error) {
      problem = String(error instanceof Error ? error.message : error);
    }
  });

  async function accept(everyStart: boolean): Promise<void> {
    settings = await client!.request<Settings>("accept_disclaimer", { every_start: everyStart });
    page = "library";
  }

  async function setAppearance(appearance: Appearance): Promise<void> {
    settings = await client!.request<Settings>("set_settings", { appearance });
    paint(appearance);
  }

  async function start(workflow: string, restart = ""): Promise<void> {
    if (!headset || running) return;
    const { run: id } = await client!.request<{ run: string }>("start", {
      codename: headset.codename,
      workflow,
      ...(restart ? { restart } : {}),
    });
    const state = initial(id);
    for (const event of early.splice(0)) apply(state, event);
    run = state;
    page = "run";
  }

  function open(entry: HeadsetEntry): void {
    codename = entry.codename;
    page = "headset";
  }

  function leaveRun(): void {
    if (run && !running) run = null; // a finished run is closed; a running one keeps going
    page = headset ? "headset" : "library";
  }
</script>

<div class="shell">
  <header class="topbar">
    <button class="brand" onclick={() => page !== "loading" && page !== "welcome" && (page = "library")}>
      <img src={logo} alt="" width="26" height="26" />overpressure
    </button>
    <div class="actions">
      <a class="iconbtn" href="https://github.com/overpressure/overpressure" target="_blank" rel="noreferrer" title="GitHub"
        ><svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-label="GitHub"
          ><path
            d="M12 .9a11.1 11.1 0 0 0-3.5 21.7c.55.1.75-.24.75-.53v-2.07c-3.06.66-3.7-1.3-3.7-1.3-.5-1.28-1.23-1.62-1.23-1.62-1-.68.08-.67.08-.67 1.1.08 1.68 1.13 1.68 1.13.98 1.68 2.57 1.2 3.2.92.1-.7.38-1.2.7-1.48-2.45-.28-5.02-1.22-5.02-5.46 0-1.21.43-2.2 1.13-2.97-.11-.28-.49-1.4.11-2.92 0 0 .93-.3 3.04 1.13a10.5 10.5 0 0 1 5.54 0c2.1-1.43 3.03-1.13 3.03-1.13.6 1.52.22 2.64.11 2.92.7.77 1.13 1.76 1.13 2.97 0 4.25-2.58 5.18-5.04 5.45.4.34.75 1.02.75 2.05v3.05c0 .3.2.64.76.53A11.1 11.1 0 0 0 12 .9Z"
          /></svg
        ></a
      >
      <div class="menu-wrap">
        <button class="iconbtn" title="Settings" aria-expanded={menuOpen} onclick={() => (menuOpen = !menuOpen)}
          ><Icon name="settings" /></button
        >
        {#if menuOpen && settings}
          <div class="menu" role="menu">
            <label class="row"
              >Appearance
              <select
                value={settings.appearance}
                onchange={(e) => setAppearance(e.currentTarget.value as Appearance)}
              >
                <option value="system">System</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select></label
            >
            <hr />
            <button
              role="menuitem"
              onclick={() => {
                menuOpen = false;
                page = "welcome";
              }}>Show the welcome page</button
            >
          </div>
        {/if}
      </div>
    </div>
  </header>

  {#if page === "headset" || page === "run"}
    <nav class="crumbs" aria-label="Where you are">
      <button class="iconbtn" title="Back" onclick={() => (page === "run" ? leaveRun() : (page = "library"))}
        ><Icon name="left" /></button
      >
      <button class="crumb" onclick={() => (page === "run" ? leaveRun() : (page = "library"))}>Headsets</button>
      <span class="muted">›</span>
      <button class="crumb current" onclick={() => page === "run" && leaveRun()}>{headset?.name ?? run?.headset}</button>
    </nav>
  {/if}

  <main class="page">
    {#if problem}
      <div class="callout bad"><Icon name="alert" /><div><h3>The engine did not answer</h3><p>{problem}</p></div></div>
    {:else if page === "loading"}
      <p class="muted">Starting…</p>
    {:else if page === "welcome" && hello && settings}
      <Welcome disclaimer={hello.disclaimer} everyStart={settings.disclaimer_every_start} {logo} onaccept={accept} />
    {:else if page === "library"}
      <Library {headsets} onopen={open} />
    {:else if page === "headset" && headset}
      <Headset {headset} {running} onstart={(w) => start(w)} onshowrun={() => (page = "run")} />
    {:else if page === "run" && run && client}
      <Run bind:run {client} onleave={leaveRun} onretry={(w, restart) => { run = null; void start(w, restart); }} />
    {/if}
  </main>

  <footer class="status">
    {#if running}
      <button class="status-run" onclick={() => (page = "run")}
        ><span class="dot"></span>{run?.headset}: {progress}% · Open</button
      >
    {:else}
      <span>Ready</span>
    {/if}
    {#if hello?.demo}<span class="muted">Demo · nothing touches a headset</span>{/if}
  </footer>
</div>

<style>
  .shell {
    height: 100%;
    display: flex;
    flex-direction: column;
    background: var(--bg);
  }

  .topbar {
    height: 52px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 24px;
    background: var(--chrome);
    border-bottom: 1px solid var(--line);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 9px;
    font-size: 14px;
    font-weight: 650;
    padding: 0;
  }

  .actions {
    display: flex;
    gap: 4px;
    align-items: center;
  }

  .menu-wrap {
    position: relative;
  }

  .menu {
    position: absolute;
    right: 0;
    top: 38px;
    z-index: 5;
    width: 240px;
    padding: 5px;
    border-radius: 9px;
    border: 1px solid var(--line);
    background: var(--panel);
    box-shadow: 0 5px 18px var(--shadow);
  }

  .menu .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 10px;
  }

  .menu select {
    background: var(--button);
    border: 1px solid var(--line);
    border-radius: 5px;
    padding: 3px 6px;
  }

  .menu hr {
    border: 0;
    border-top: 1px solid var(--line);
    margin: 4px 0;
  }

  .menu button {
    width: 100%;
    text-align: left;
    padding: 9px 10px;
    border-radius: 5px;
  }

  .menu button:hover {
    background: var(--hover);
  }

  .crumbs {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 24px 0;
    flex-shrink: 0;
  }

  .crumb {
    padding: 4px 2px;
    color: var(--muted);
    font-size: 13px;
  }

  .crumb:hover {
    color: var(--blue);
  }

  .crumb.current {
    color: var(--text);
    font-weight: 600;
  }

  .page {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 24px 32px 32px;
    scrollbar-width: thin;
    scrollbar-color: var(--line) transparent;
  }

  .status {
    height: 32px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 24px;
    font-size: 11px;
    border-top: 1px solid var(--line);
  }

  .status-run {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 11px;
    padding: 0;
  }

  .status-run:hover {
    color: var(--blue);
  }

  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--blue);
  }

  @media (max-width: 680px) {
    .page {
      padding: 20px;
    }

    .topbar,
    .status {
      padding: 0 16px;
    }

    .crumbs {
      padding: 8px 16px 0;
    }
  }
</style>
