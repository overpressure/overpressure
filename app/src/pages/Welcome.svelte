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
  <img src={logo} alt="" width="56" height="56" />
  <h1>Welcome to overpressure</h1>
  <div class="callout">
    <Icon name="alert" size={22} />
    <div>
      <h3>{disclaimer.title}</h3>
      {#each paragraphs as paragraph, i (i)}<p>{paragraph}</p>{/each}
    </div>
  </div>
  <label class="check"><input type="checkbox" bind:checked={every} />Show this on every start</label>
  <button class="btn primary" onclick={() => onaccept(every)}>{disclaimer.accept} &amp; continue</button>
</section>

<style>
  .welcome {
    max-width: 500px;
    margin: 24px auto;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
  }

  img {
    margin-bottom: 20px;
  }

  .callout {
    margin: 24px 0 20px;
  }

  .callout p {
    font-size: 13px;
    line-height: 1.55;
  }

  .callout p + p {
    margin-top: 8px;
  }

  .check {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 24px;
  }

  .check input {
    width: 15px;
    height: 15px;
    margin: 0;
    accent-color: var(--primary);
  }

  .btn {
    align-self: flex-end;
  }
</style>
