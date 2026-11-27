<!-- SPDX-License-Identifier: Unlicense -->
<!-- The first setup, right after the disclaimer on a first start: an earlier overpressure's data (its backup,
     settings and installer records). It looks where a portable overpressure usually is (the folder beside a
     release's; Downloads, the desktop, documents and home for the app) and offers what it found, or the owner
     chooses the folder, or starts fresh. The headsets come after it. -->
<script lang="ts">
  import { onMount } from "svelte";
  import Icon from "../components/Icon.svelte";
  import { app, type CarryOffer } from "../lib/app.svelte";
  import { pickPath } from "../lib/bridge";

  let { logo }: { logo: string } = $props();

  /** What the search found; null while it looks. */
  let offer = $state<CarryOffer | null>(null);
  /** The folder to bring over: the one found, or the one the owner chose (checked by the engine). */
  let chosen = $state<{ from: string; question: string } | null>(null);
  let working = $state(false);
  let brought = $state<string[] | null>(null);
  let failure = $state("");
  const paragraphs = $derived((chosen?.question ?? "").split("\n\n").filter(Boolean));

  onMount(async () => {
    try {
      offer = await app.engine.request<CarryOffer>("carry_over");
      if (offer.from) chosen = { from: offer.from, question: offer.question };
      else if (!offer.ask) app.finishSetup(); // nothing to ask about
    } catch (error) {
      offer = { from: "", question: "", ask: true };
      failure = error instanceof Error ? error.message : String(error);
    }
  });

  async function choose(): Promise<void> {
    const folder = await pickPath({ folder: true, title: "Your earlier overpressure's folder" });
    if (!folder) return;
    failure = "";
    try {
      chosen = await app.engine.request<{ from: string; question: string }>("carry_check", { from: folder });
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    }
  }

  async function carry(): Promise<void> {
    if (!chosen) return;
    working = true;
    failure = "";
    try {
      brought = (await app.engine.request<{ brought: string[] }>("carry", { from: chosen.from })).brought;
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    } finally {
      working = false;
    }
  }

  async function fresh(): Promise<void> {
    working = true;
    try {
      await app.engine.request("decline_carry_over", { from: chosen?.from ?? "" });
      app.finishSetup();
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    } finally {
      working = false;
    }
  }
</script>

<section class="setup">
  <header>
    <img src={logo} alt="" width="64" height="64" />
    <h2 class="title">{brought ? "All set" : chosen ? "Your earlier overpressure" : "Used overpressure before?"}</h2>
  </header>

  <div class="card">
    {#if brought}
      <p>Brought over:</p>
      <ul>{#each brought as what, i (i)}<li>{what}</li>{/each}</ul>
    {:else if chosen}
      {#each paragraphs as paragraph, i (i)}<p class:muted={i > 0}>{paragraph}</p>{/each}
    {:else}
      <p>
        If you used overpressure from its download before (the folder with Install.cmd in it), point to that folder:
        your headset's backup, your settings and what the installer knows about your headset come over.
      </p>
      <p class="muted">
        The backup is your way back to your headset's own system. Without it this overpressure starts from nothing,
        and its installer makes a new backup first.
      </p>
      {#if !offer}
        <p class="looking" role="status"><span class="spinner" aria-hidden="true"></span>Looking for it on this computer…</p>
      {:else if offer.ask}
        <p class="muted">None was found in Downloads, on the desktop, in Documents or in your home folder.</p>
      {/if}
    {/if}
    {#if failure}<p class="callout bad"><Icon name="alert" /> {failure}</p>{/if}
  </div>

  <div class="foot">
    {#if brought}
      <span class="grow"></span>
      <button class="btn primary" onclick={() => app.finishSetup()}>Continue</button>
    {:else}
      <button class="btn ghost" disabled={working} onclick={fresh}>Start fresh</button>
      <span class="grow"></span>
      {#if chosen}
        {#if offer?.ask}<button class="btn ghost" disabled={working} onclick={choose}>Another folder…</button>{/if}
        <button class="btn primary" disabled={working} onclick={carry}
          >{working ? "Bringing it over…" : "Bring it over"}</button
        >
      {:else}
        <button class="btn primary" disabled={working} onclick={choose}>Choose its folder…</button>
      {/if}
    {/if}
  </div>
</section>

<style>
  .setup {
    max-width: 520px;
    margin: 16px auto;
    display: grid;
    gap: 24px;
  }

  header {
    display: grid;
    justify-items: center;
    gap: 16px;
    text-align: center;
  }

  .title {
    font-size: 26px;
    font-weight: 650;
    letter-spacing: -0.65px;
  }

  .card {
    display: grid;
    gap: 10px;
    padding: 16px 18px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--chrome);
    font-size: 13px;
    line-height: 1.55;
    overflow-wrap: anywhere;
  }

  .card .muted {
    font-size: 12px;
  }

  ul {
    margin: 0;
    padding-left: 18px;
  }

  .looking {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 12px;
  }

  .spinner {
    width: 12px;
    height: 12px;
    border: 2px solid var(--track);
    border-top-color: var(--blue);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .foot {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .grow {
    flex: 1;
  }
</style>
