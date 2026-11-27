// SPDX-License-Identifier: Unlicense
// Tooltips: an element's data-tip shows on hover and on keyboard focus, in one floating box on the page's top layer,
// so no container's edge or overflow cuts it. It opens above the element (.tip-below: below), flips when there is no
// room, and moves sideways to stay whole inside the window. The element keeps the same words for screen readers
// (aria-label or a described-by text): the box itself is hidden from them.

const MARGIN = 8; // the least distance to the window's edge
const GAP = 6; // between the element and the box

export interface Box {
  left: number;
  top: number;
  below: boolean;
}

/** Return where a box of WIDTH x HEIGHT goes for an element at RECT in a VIEW-sized window. */
export function place(
  rect: { left: number; top: number; width: number; height: number },
  width: number,
  height: number,
  view: { width: number; height: number },
  preferBelow = false,
): Box {
  const roomAbove = rect.top - GAP - MARGIN;
  const roomBelow = view.height - (rect.top + rect.height) - GAP - MARGIN;
  const fitsAbove = roomAbove >= height;
  const fitsBelow = roomBelow >= height;
  const below = preferBelow
    ? fitsBelow || (!fitsAbove && roomBelow >= roomAbove)
    : !fitsAbove && (fitsBelow || roomBelow > roomAbove);
  let top = below ? rect.top + rect.height + GAP : rect.top - GAP - height;
  top = Math.max(MARGIN, Math.min(top, view.height - MARGIN - height));
  let left = rect.left + rect.width / 2 - width / 2;
  left = Math.max(MARGIN, Math.min(left, view.width - MARGIN - width));
  return { left, top, below };
}

let box: HTMLDivElement | undefined;
let owner: HTMLElement | null = null;

function show(target: HTMLElement): void {
  const text = target.dataset.tip;
  if (!text) return hide();
  box ??= Object.assign(document.createElement("div"), { className: "tooltip", role: "presentation" });
  box.setAttribute("aria-hidden", "true");
  if (!box.isConnected) document.body.append(box);
  owner = target;
  box.textContent = text;
  box.style.maxWidth = `${Math.min(260, window.innerWidth - 2 * MARGIN)}px`;
  box.hidden = false;
  const { width, height } = box.getBoundingClientRect();
  const where = place(
    target.getBoundingClientRect(),
    width,
    height,
    { width: window.innerWidth, height: window.innerHeight },
    target.classList.contains("tip-below"),
  );
  box.style.left = `${Math.round(where.left)}px`;
  box.style.top = `${Math.round(where.top)}px`;
}

function hide(): void {
  owner = null;
  if (box) box.hidden = true;
}

function tipped(node: EventTarget | null): HTMLElement | null {
  return node instanceof Element ? (node.closest<HTMLElement>("[data-tip]") ?? null) : null;
}

/** Start the tooltips for the whole page: once. */
export function installTooltips(): void {
  document.addEventListener("pointerover", (e) => {
    const target = tipped(e.target);
    if (target !== owner) target ? show(target) : hide();
  });
  document.addEventListener("pointerleave", hide);
  document.addEventListener("focusin", (e) => {
    const target = tipped(e.target);
    if (target && target.matches(":focus-visible")) show(target);
    else if (!target) hide();
  });
  document.addEventListener("focusout", () => hide());
  document.addEventListener("pointerdown", hide);
  document.addEventListener("keydown", (e) => e.key === "Escape" && hide());
  window.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
  // A dialog opened over the page is the top layer: the box goes into it, or the dialog's backdrop hides it.
  new MutationObserver(() => {
    const open = [...document.querySelectorAll<HTMLDialogElement>("dialog[open]")].at(-1);
    const parent = open ?? document.body;
    if (box && box.parentElement !== parent) parent.append(box);
  }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["open"], childList: true });
}
