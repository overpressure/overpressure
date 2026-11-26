<!-- SPDX-License-Identifier: Unlicense -->
<!-- Line icons on a 24 px grid, drawn with the text's colour. -->
<script lang="ts" module>
  const PATHS = {
    left: "m15 5-7 7 7 7M8 12h13",
    right: "m9 6 6 6-6 6",
    plus: "M12 5v14M5 12h14",
    close: "m6 6 12 12M18 6 6 18",
    check: "m5 12 4 4L19 6",
    up: "M12 19V5m-6 6 6-6 6 6",
    download: "M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4",
    restore: "M3 11a9 9 0 1 1 3 7M3 4v7h7M12 7v5l3 2",
    shield: "M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3ZM8 12l3 3 5-6",
    alert: "M10.3 4.5 2.5 18a1.5 1.5 0 0 0 1.3 2.3h16.4a1.5 1.5 0 0 0 1.3-2.3L13.7 4.5a2 2 0 0 0-3.4 0ZM12 9v4m0 3h.01",
    info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 11v5m0-8h.01",
    settings:
      "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z",
    stop: "M7 7h10v10H7z",
    terminal: "m4 17 6-5-6-5M12 19h8",
    search: "M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM16 16l4 4",
    copy: "M9 9h10v11H9zM5 15V4h10",
    archive: "M3 5h18v4H3zM5 9v10h14V9M10 13h4",
    file: "M14 3H6v18h12V7l-4-4ZM14 3v4h4M9 13h6M9 17h6",
    sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M14 5v4M8 15v4",
    refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
    upload: "M12 15V3m-5 5 5-5 5 5M4 16v4h16v-4",
    lock: "M6 11h12v9H6zM9 11V8a3 3 0 0 1 6 0v3",
    branch: "M7 4v12M7 16a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM17 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM17 8c0 5-10 3-10 8",
    pin: "M9 4h6l-1 6 3 3H7l3-3-1-6ZM12 13v7",
  } as const;

  export type IconName = keyof typeof PATHS;
</script>

<script lang="ts">
  let { name, size = 17 }: { name: IconName; size?: number } = $props();
</script>

<svg
  class="icon"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.7"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"><path d={PATHS[name]} /></svg
>
