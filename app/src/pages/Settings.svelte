<!-- SPDX-License-Identifier: Unlicense -->
<!-- The owner's settings: what really is theirs to choose, nothing more. The appearance, whether the disclaimer
     shows on every start, their own SSH key for the installed system, and where overpressure keeps its data.
     What overpressure finds out about a headset (its serials, where its backup is) is recorded, not set here.
     Each change is kept at once (settings.json in the data folder). -->
<script lang="ts">
  import Icon from "../components/Icon.svelte";
  import { app } from "../lib/app.svelte";
  import { pickPath } from "../lib/bridge";
  import type { Appearance, Settings } from "../lib/protocol";

  let { settings }: { settings: Settings } = $props();

  let problem = $state("");

  async function change(fields: Parameters<typeof app.setSettings>[0]): Promise<void> {
    problem = "";
    try {
      await app.setSettings(fields);
    } catch (error) {
      problem = error instanceof Error ? error.message : String(error);
    }
  }

  async function chooseKey(): Promise<void> {
    const path = await pickPath({
      title: "Your public SSH key",
      suggested: settings.ssh_public_key || undefined,
      filters: [{ name: "Public key", extensions: ["pub"] }],
    });
    if (path) await change({ ssh_public_key: path });
  }

  async function openFolder(): Promise<void> {
    try {
      await app.engine.request("open_folder");
    } catch (error) {
      app.say(error instanceof Error ? error.message : String(error));
    }
  }

  const APPEARANCES: { value: Appearance; label: string }[] = [
    { value: "system", label: "System" },
    { value: "light", label: "Light" },
    { value: "dark", label: "Dark" },
  ];
</script>

<div class="settings">
  <section class="group" aria-labelledby="look">
    <h2 id="look">The app</h2>
    <div class="row">
      <div class="what">
        <span class="name" id="appearance">Appearance</span>
        <span class="help">Light, dark, or as the system is.</span>
      </div>
      <div class="segmented" role="radiogroup" aria-labelledby="appearance">
        {#each APPEARANCES as a (a.value)}
          <button
            role="radio"
            aria-checked={settings.appearance === a.value}
            class:on={settings.appearance === a.value}
            onclick={() => change({ appearance: a.value })}>{a.label}</button
          >
        {/each}
      </div>
    </div>
    <label class="row">
      <div class="what">
        <span class="name">Show the disclaimer on every start</span>
        <span class="help">Off: it shows once for each new version of overpressure.</span>
      </div>
      <input
        type="checkbox"
        class="switch"
        checked={settings.disclaimer_every_start}
        onchange={(e) => change({ disclaimer_every_start: e.currentTarget.checked })}
      />
    </label>
  </section>

  <section class="group" aria-labelledby="headset">
    <h2 id="headset">Your headset</h2>
    <div class="row column">
      <div class="what">
        <span class="name">Your own SSH key</span>
        <span class="help"
          >The installed system takes SSH keys only. This computer's key is enrolled anyway; your own public key
          (a .pub file) lets you log in from other computers too. It takes effect with the next install or
          upgrade.</span
        >
      </div>
      <div class="value">
        <code class:none={!settings.ssh_public_key}>{settings.ssh_public_key || "None"}</code>
        <div class="buttons">
          {#if settings.ssh_public_key}
            <button class="btn ghost" onclick={() => change({ ssh_public_key: "" })}>Remove</button>
          {/if}
          <button class="btn secondary" onclick={chooseKey}>Choose…</button>
        </div>
      </div>
      {#if problem}<p class="problem" role="alert"><Icon name="alert" size={14} /> {problem}</p>{/if}
    </div>
  </section>

  <section class="group" aria-labelledby="data">
    <h2 id="data">Data</h2>
    <div class="row column">
      <div class="what">
        <span class="name">Data folder</span>
        <span class="help"
          >Your headset's backup, the downloaded headsets, what overpressure found out about your headset and
          these settings.</span
        >
      </div>
      <div class="value">
        <code>{settings.folder}</code>
        <div class="buttons">
          <button class="btn secondary" onclick={openFolder}><Icon name="file" size={14} /> Open</button>
        </div>
      </div>
    </div>
  </section>
</div>

<style>
  .settings {
    max-width: 680px;
    margin: 0 auto;
    display: grid;
    gap: 20px;
  }

  .group {
    display: grid;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--panel);
    overflow: hidden;
  }

  h2 {
    padding: 14px 18px 6px;
    color: var(--muted);
    font-size: 12px;
    font-weight: 650;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 12px 18px;
  }

  .row + .row {
    border-top: 1px solid var(--line);
  }

  label.row {
    cursor: pointer;
  }

  .row.column {
    display: grid;
    gap: 10px;
  }

  .what {
    flex: 1;
    min-width: 0;
    display: grid;
    gap: 3px;
  }

  .name {
    font-weight: 600;
  }

  .help {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }

  .segmented {
    display: inline-flex;
    padding: 2px;
    border-radius: 8px;
    background: var(--inset);
    border: 1px solid var(--line);
    flex-shrink: 0;
  }

  .segmented button {
    padding: 5px 12px;
    border-radius: 6px;
    color: var(--muted);
    font-size: 12px;
  }

  .segmented button.on {
    background: var(--button);
    color: var(--text);
    font-weight: 600;
    box-shadow: 0 1px 2px rgb(0 0 0 / 0.15);
  }

  .switch {
    width: 18px;
    height: 18px;
    accent-color: var(--blue);
    flex-shrink: 0;
  }

  .value {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 12px;
  }

  code {
    flex: 1;
    min-width: 0;
    padding: 7px 10px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: var(--inset);
    font: 12px/1.4 ui-monospace, "Cascadia Mono", Menlo, monospace;
    overflow-wrap: anywhere;
  }

  code.none {
    color: var(--muted);
    font-family: inherit;
  }

  .buttons {
    display: flex;
    gap: 8px;
  }

  .problem {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--red);
    font-size: 12px;
  }
</style>
