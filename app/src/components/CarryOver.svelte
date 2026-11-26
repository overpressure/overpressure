<!-- SPDX-License-Identifier: Unlicense -->
<!-- An earlier overpressure folder beside this one: bring its backup, settings and records over, or start fresh. -->
<script lang="ts">
  import { app } from "../lib/app.svelte";
  import Icon from "./Icon.svelte";

  let { offer }: { offer: { from: string; question: string } } = $props();

  let dialog = $state<HTMLDialogElement>();
  let working = $state(false);
  let brought = $state<string[] | null>(null);
  let failure = $state("");
  const paragraphs = $derived(offer.question.split("\n\n").filter(Boolean));

  $effect(() => {
    dialog?.showModal();
  });

  async function carry(): Promise<void> {
    working = true;
    failure = "";
    try {
      brought = (await app.engine.request<{ brought: string[] }>("carry", { from: offer.from })).brought;
      await app.refresh();
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    } finally {
      working = false;
    }
  }

  async function decline(): Promise<void> {
    await app.engine.request("decline_carry_over", { from: offer.from });
    close();
  }

  function close(): void {
    dialog?.close();
    app.carryOver = null;
  }
</script>

<dialog bind:this={dialog} aria-labelledby="carry-title" oncancel={(e) => e.preventDefault()}>
  <div class="title"><h2 id="carry-title">Your earlier overpressure</h2></div>
  <div class="body">
    {#if brought}
      <p>Brought over:</p>
      <ul>{#each brought as what, i (i)}<li>{what}</li>{/each}</ul>
    {:else}
      {#each paragraphs as paragraph, i (i)}<p class:muted={i > 0}>{paragraph}</p>{/each}
      {#if failure}<p class="callout bad"><Icon name="alert" /> {failure}</p>{/if}
    {/if}
  </div>
  <div class="foot">
    {#if brought}
      <button class="btn primary" onclick={close}>Done</button>
    {:else}
      <button class="btn ghost" disabled={working} onclick={decline}>Start fresh</button>
      <button class="btn primary" disabled={working} onclick={carry}>{working ? "Bringing it over…" : "Bring it over"}</button>
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
