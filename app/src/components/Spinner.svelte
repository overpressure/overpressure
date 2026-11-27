<!-- SPDX-License-Identifier: Unlicense -->
<!-- A wait whose length cannot be measured (a look, a search, an answer from the network): it turns until it ends.
     A wait that can be measured gets a bar instead (JobStatus). -->
<script lang="ts">
  let { size = 12 }: { size?: number } = $props();
</script>

<span class="spinner" style:width="{size}px" style:height="{size}px" aria-hidden="true"></span>

<style>
  .spinner {
    display: inline-block;
    flex-shrink: 0;
    box-sizing: border-box;
    border: 2px solid var(--track);
    border-top-color: var(--blue);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: -2px;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation-duration: 2.4s;
    }
  }
</style>
