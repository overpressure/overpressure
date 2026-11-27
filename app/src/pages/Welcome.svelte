<!-- SPDX-License-Identifier: Unlicense -->
<!-- The welcome page: the disclaimer, accepted before anything else; "Show this on every start" unticked,
     it comes back with each new version of overpressure. -->
<script lang="ts">
  import Icon from "../components/Icon.svelte";
  import type { Hello } from "../lib/protocol";

  interface Props {
    disclaimer: Hello["disclaimer"];
    everyStart: boolean;
    logo: string;
    onaccept: (everyStart: boolean) => void;
  }

  let { disclaimer, everyStart, logo, onaccept }: Props = $props();
  let every = $state(true);
  $effect.pre(() => {
    every = everyStart;
  });
  const paragraphs = $derived(disclaimer.text.split("\n\n"));
</script>

<section class="welcome">
  <header>
    <img src={logo} alt="" width="64" height="64" />
    <h2 class="title">Welcome to overpressure!</h2>
  </header>
  <div class="callout">
    <Icon name="alert" size={22} />
    <div>
      <h3>{disclaimer.title}</h3>
      {#each paragraphs as paragraph, i (i)}<p>{paragraph}</p>{/each}
    </div>
  </div>
  <div class="foot">
    <label class="check"><input type="checkbox" bind:checked={every} />Show this on every start</label>
    <button class="btn primary" onclick={() => onaccept(every)}>{disclaimer.accept} &amp; continue</button>
  </div>
</section>

<style>
  .welcome {
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

  .callout p {
    font-size: 13px;
    line-height: 1.55;
  }

  .callout p + p {
    margin-top: 8px;
  }

  .foot {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .check {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .check input {
    width: 15px;
    height: 15px;
    margin: 0;
    accent-color: var(--primary);
  }
</style>
