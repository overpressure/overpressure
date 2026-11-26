<!-- SPDX-License-Identifier: Unlicense -->
<!-- The window: the top bar, the breadcrumb on inner pages, the page, and the status bar. A run or a job keeps
     going while the pages change: only Stop stops it, and the status bar leads back to a run. -->
<script lang="ts">
  import { onMount } from "svelte";
  import logo from "../../docs/logo/overpressure.svg?url";
  import { app } from "./lib/app.svelte";
  import { jobLine } from "./lib/job";
  import type { Appearance } from "./lib/protocol";
  import { overall } from "./lib/run";
  import CarryOver from "./components/CarryOver.svelte";
  import Icon from "./components/Icon.svelte";
  import StartSheet from "./components/StartSheet.svelte";
  import Advanced from "./pages/Advanced.svelte";
  import Headset from "./pages/Headset.svelte";
  import Library from "./pages/Library.svelte";
  import Run from "./pages/Run.svelte";
  import Welcome from "./pages/Welcome.svelte";

  let menuOpen = $state(false);

  const progress = $derived(app.run ? Math.round(overall(app.run).fraction * 100) : 0);
  const job = $derived(app.job ? app.jobs[app.job] : undefined);
  const inner = $derived(app.page === "headset" || app.page === "run" || app.page === "advanced");

  const systemDark = matchMedia("(prefers-color-scheme: dark)");
  function paint(appearance: Appearance): void {
    document.documentElement.dataset.theme =
      appearance === "system" ? (systemDark.matches ? "dark" : "light") : appearance;
  }
  systemDark.addEventListener("change", () => paint(app.settings?.appearance ?? "system"));
  $effect(() => paint(app.settings?.appearance ?? "system"));

  onMount(() => void app.start());

  function back(): void {
    if (app.page === "run") app.leaveRun();
    else if (app.page === "advanced") app.page = "headset";
    else app.page = "library";
  }

  function menu(action: () => void): void {
    menuOpen = false;
    action();
  }
</script>

<svelte:window onclick={(e) => menuOpen && !(e.target as HTMLElement).closest(".menu-wrap") && (menuOpen = false)} />

<div class="shell">
  <header class="topbar">
    <button
      class="brand"
      onclick={() => {
        if (app.page !== "loading" && app.page !== "welcome") app.page = "library";
      }}
    >
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
        {#if menuOpen && app.settings}
          <div class="menu" role="menu">
            <label class="row"
              >Appearance
              <select
                value={app.settings.appearance}
                onchange={(e) => app.setAppearance(e.currentTarget.value as Appearance)}
              >
                <option value="system">System</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select></label
            >
            <hr />
            {#if app.headset?.has_steps}
              <button role="menuitem" onclick={() => menu(() => app.openAdvanced())}>Advanced mode</button>
            {/if}
            <button role="menuitem" onclick={() => menu(() => (app.page = "welcome"))}>Show the welcome page</button>
          </div>
        {/if}
      </div>
    </div>
  </header>

  {#if inner}
    <nav class="crumbs" aria-label="Where you are">
      <button class="iconbtn" title="Back" onclick={back}><Icon name="left" /></button>
      <button class="crumb" onclick={() => (app.page === "run" ? app.leaveRun() : (app.page = "library"))}>Headsets</button>
      <span class="muted">›</span>
      <button
        class="crumb"
        class:current={app.page === "headset"}
        onclick={() => (app.page === "run" ? app.leaveRun() : (app.page = "headset"))}
        >{app.headset?.name ?? app.run?.headset}</button
      >
      {#if app.page === "advanced"}<span class="muted">›</span><span class="crumb current">Advanced mode</span>{/if}
    </nav>
  {/if}

  <main class="page">
    {#if app.problem}
      <div class="callout bad"><Icon name="alert" /><div><h3>The engine did not answer</h3><p>{app.problem}</p></div></div>
    {:else if app.page === "loading"}
      <p class="muted">Starting…</p>
    {:else if app.page === "welcome" && app.hello && app.settings}
      <Welcome
        disclaimer={app.hello.disclaimer}
        everyStart={app.settings.disclaimer_every_start}
        {logo}
        onaccept={(every) => app.accept(every)}
      />
    {:else if app.page === "library"}
      <Library />
    {:else if app.page === "headset" && app.headset}
      <Headset headset={app.headset} />
    {:else if app.page === "advanced" && app.headset}
      <Advanced headset={app.headset} />
    {:else if app.page === "run" && app.run}
      <Run />
    {/if}
  </main>

  <footer class="status">
    {#if app.running}
      <button class="status-run" onclick={() => (app.page = "run")}
        ><span class="dot"></span>{app.run?.headset}: {progress}% · Open</button
      >
    {:else if job && job.code === null}
      <span class="status-run"><span class="dot"></span>{jobLine(job)}</span>
    {:else if app.notice}
      <span role="status">{app.notice}</span>
    {:else}
      <span>Ready</span>
    {/if}
    {#if app.hello?.demo}<span class="muted">Demo · nothing touches a headset</span>{/if}
  </footer>
</div>

{#if app.sheet}<StartSheet sheet={app.sheet} />{/if}
{#if app.carryOver}<CarryOver offer={app.carryOver} />{/if}

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

  button.crumb:hover {
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
    gap: 16px;
    padding: 0 24px;
    font-size: 11px;
    border-top: 1px solid var(--line);
  }

  .status > :first-child {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .status-run {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 11px;
    padding: 0;
  }

  button.status-run:hover {
    color: var(--blue);
  }

  .dot {
    width: 7px;
    height: 7px;
    flex-shrink: 0;
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
