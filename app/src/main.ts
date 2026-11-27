// SPDX-License-Identifier: Unlicense
import "@fontsource-variable/inter";
import "./app.css";
import { mount } from "svelte";
import App from "./App.svelte";
import { installTooltips } from "./lib/tooltip";

installTooltips();
mount(App, { target: document.getElementById("app") as HTMLElement });
