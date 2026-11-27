<!-- SPDX-License-Identifier: Unlicense -->
<!-- An earlier overpressure's data: the folder beside this one, or the one the app found or the owner points to (the
     portable zip). Bring its backup, settings and records over, or start fresh. -->
<script lang="ts">
  import { app, type CarryOffer } from "../lib/app.svelte";
  import { pickPath } from "../lib/bridge";
  import Icon from "./Icon.svelte";

  let { offer }: { offer: CarryOffer } = $props();

  let dialog = $state<HTMLDialogElement>();
  let working = $state(false);
  let brought = $state<string[] | null>(null);
  let failure = $state("");
  /** The folder to bring over: the one found, or the one the owner chose (checked by the engine). */
  // svelte-ignore state_referenced_locally
  let chosen = $state<{ from: string; question: string } | null>(offer.from ? offer : null);
  const paragraphs = $derived((chosen?.question ?? "").split("\n\n").filter(Boolean));

  $effect(() => {
    dialog?.showModal();
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
      await app.refresh();
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    } finally {
      working = false;
    }
  }

  async function decline(): Promise<void> {
    await app.engine.request("decline_carry_over", { from: chosen?.from ?? "" });
    close();
  }

  function close(): void {
    dialog?.close();
    app.carryOver = null;
  }
</script>

<dialog bind:this={dialog} aria-labelledby="carry-title" oncancel={(e) => e.preventDefault()}>
  <div class="title"><h2 id="carry-title">{chosen ? "Your earlier overpressure" : "Used overpressure before?"}</h2></div>
  <div class="body">
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
    {/if}
    {#if failure}<p class="callout bad"><Icon name="alert" /> {failure}</p>{/if}
  </div>
  <div class="foot">
    {#if brought}
      <button class="btn primary" onclick={close}>Done</button>
    {:else}
      <button class="btn ghost" disabled={working} onclick={decline}>Start fresh</button>
      {#if chosen}
        {#if offer.ask}<button class="btn ghost" disabled={working} onclick={choose}>Another folder…</button>{/if}
        <button class="btn primary" disabled={working} onclick={carry}>{working ? "Bringing it over…" : "Bring it over"}</button>
      {:else}
        <button class="btn primary" onclick={choose}>Choose its folder…</button>
      {/if}
    {/if}
  </div>
</dialog>

<style>
  .title {
    padding: 16px 20px 8px;
  }

  .body {
    padding: 0 20px 16px;
    display: grid;
    gap: 10px;
    font-size: 13px;
  }

  .body .muted {
    font-size: 12px;
  }

  ul {
    margin: 0;
    padding-left: 18px;
  }

  .foot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid var(--line);
  }
</style>
