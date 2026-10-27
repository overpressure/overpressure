"""overpressure's graphical tool: a headset's image creation and flashing steps, one click each.

Every step is a command line of overpressure or a script of the headset (overpressure.headset.
Step); the window shows that command line as it will run, so whatever the GUI does can be
repeated in a terminal. One step runs at a time (builds share their caches), its output goes to
the log below, and a step that writes to the headset asks first. Tk comes with Python, so the
tool needs nothing beyond the standard library.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Dict, Optional

from . import REPO, runner
from .headset import Headset, Registry, Step

GROUPS = ("Image", "Headset")


class App:
    def __init__(self, root: tk.Tk, registry: Registry, headset: Optional[Headset]):
        self.root, self.registry = root, registry
        self.headset = headset or next(iter(registry.headsets.values()))
        self.values: Dict[str, Dict[str, str]] = {}
        self.vars: Dict[str, tk.Variable] = {}
        self.step: Optional[Step] = None
        self.proc: Optional[subprocess.Popen] = None
        self.running = False  # one step at a time
        self.lines: "queue.Queue[str]" = queue.Queue()
        root.title("overpressure")
        root.geometry("1100x760")
        root.minsize(800, 560)
        self._build()
        self._load_headset()
        root.after(100, self._drain)

    # -- layout --------------------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Headset").pack(side="left")
        names = [f"{h.info.name} ({c})" for c, h in sorted(self.registry.headsets.items())]
        self.device = ttk.Combobox(top, values=names, state="readonly", width=28)
        self.device.pack(side="left", padx=(6, 12))
        self.device.bind("<<ComboboxSelected>>", self._on_device)
        self.about = ttk.Label(top, foreground="#555")
        self.about.pack(side="left")

        panes = ttk.PanedWindow(self.root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        upper = ttk.PanedWindow(panes, orient="horizontal")
        panes.add(upper, weight=3)

        self.tree = ttk.Treeview(upper, show="tree", selectmode="browse")
        self.tree.column("#0", width=300, stretch=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        upper.add(self.tree, weight=1)

        self.detail = ttk.Frame(upper, padding=(12, 4))
        upper.add(self.detail, weight=3)
        self.title = ttk.Label(self.detail, font=("TkDefaultFont", 12, "bold"))
        self.title.pack(anchor="w")
        self.help = ttk.Label(self.detail, wraplength=620, justify="left")
        self.help.pack(anchor="w", pady=(4, 10))
        self.form = ttk.Frame(self.detail)
        self.form.pack(fill="x")
        ttk.Label(self.detail, text="Command line").pack(anchor="w", pady=(12, 2))
        self.command = tk.Text(self.detail, height=4, wrap="char", relief="solid", borderwidth=1,
                               font=("Consolas" if sys.platform == "win32" else "Monospace", 9))
        self.command.pack(fill="x")
        buttons = ttk.Frame(self.detail)
        buttons.pack(anchor="w", pady=10)
        self.run_button = ttk.Button(buttons, text="Run", command=self._run)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        ttk.Button(buttons, text="Copy command", command=self._copy).pack(side="left")

        logframe = ttk.Frame(panes)
        panes.add(logframe, weight=2)
        self.log = ScrolledText(logframe, height=12, state="disabled", wrap="none",
                                font=("Consolas" if sys.platform == "win32" else "Monospace", 9))
        self.log.pack(fill="both", expand=True)
        self.status = ttk.Label(self.root, text="idle", padding=(10, 2), anchor="w")
        self.status.pack(fill="x")

    def _load_headset(self):
        codes = sorted(self.registry.headsets)
        self.device.current(codes.index(self.headset.info.codename))
        info = self.headset.info
        self.about.config(text=" · ".join(x for x in (info.soc, info.summary) if x))
        self.tree.delete(*self.tree.get_children())
        self.steps = {s.key: s for s in self.headset.steps()}
        for group in GROUPS:
            parent = self.tree.insert("", "end", iid="group:" + group, text=group, open=True)
            for step in self.steps.values():
                if step.group == group:
                    self.tree.insert(parent, "end", iid=step.key, text=step.title)
        first = next(iter(self.steps), None)
        if first:
            self.tree.selection_set(first)
        else:
            self._show(None)

    # -- a step --------------------------------------------------------------------------
    def _on_device(self, _event=None):
        codename = sorted(self.registry.headsets)[self.device.current()]
        self.headset = self.registry.get(codename)
        self._load_headset()

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        self._show(self.steps.get(sel[0]) if sel else None)

    def _show(self, step: Optional[Step]):
        self.step = step
        for child in self.form.winfo_children():
            child.destroy()
        self.vars = {}
        if step is None:
            self.title.config(text="")
            self.help.config(text="")
            self._update_command()
            return
        notes = []
        if step.linux_root:
            notes.append("Runs on Linux as root" + (" (WSL)." if sys.platform == "win32" else " (sudo)."))
        if step.writes:
            notes.append("Writes to the headset: asks before it starts.")
        self.title.config(text=step.title)
        self.help.config(text=" ".join([step.help, *notes]))
        values = self.values.setdefault(step.key, {p.name: p.default for p in step.params})
        for row, param in enumerate(step.params):
            ttk.Label(self.form, text=param.label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            if param.kind == "flag":
                var = tk.StringVar(value=values[param.name])
                widget = ttk.Checkbutton(self.form, variable=var, onvalue="1", offvalue="0")
            elif param.kind == "choice":
                var = tk.StringVar(value=values[param.name])
                widget = ttk.Combobox(self.form, textvariable=var, values=list(param.choices), width=50)
            else:
                var = tk.StringVar(value=values[param.name])
                widget = ttk.Entry(self.form, textvariable=var, width=60)
            widget.grid(row=row, column=1, sticky="we", pady=3)
            if param.kind == "path" and not step.linux_root:
                ttk.Button(self.form, text="…", width=3,
                           command=lambda v=var, p=param: self._browse(v, p)).grid(row=row, column=2, padx=4)
            if param.help:
                ttk.Label(self.form, text=param.help, foreground="#555").grid(row=row, column=3, sticky="w", padx=6)
            var.trace_add("write", lambda *_a, n=param.name, v=var: self._set(n, v))
            self.vars[param.name] = var
        self.form.columnconfigure(1, weight=1)
        self._update_command()

    def _browse(self, var: tk.StringVar, param):
        path = (filedialog.askopenfilename(initialdir=REPO) if "." in param.default.rsplit("/", 1)[-1]
                else filedialog.askdirectory(initialdir=REPO))
        if path:
            try:
                path = str(type(REPO)(path).relative_to(REPO)).replace("\\", "/")
            except ValueError:
                pass
            var.set(path)

    def _set(self, name: str, var: tk.Variable):
        if self.step is not None:
            self.values[self.step.key][name] = var.get()
            self._update_command()

    def _argv(self):
        return runner.process(self.headset, self.step, self.values.get(self.step.key, {}))

    def _update_command(self):
        text = runner.display(self._argv()) if self.step is not None else ""
        self.command.config(state="normal")
        self.command.delete("1.0", "end")
        self.command.insert("1.0", text)
        self.command.config(state="disabled")

    def _copy(self):
        if self.step is not None:
            self.root.clipboard_clear()
            self.root.clipboard_append(runner.display(self._argv()))

    # -- running -------------------------------------------------------------------------
    def _run(self):
        step = self.step
        if step is None or self.running:
            return
        argv = self._argv()
        if step.writes and not messagebox.askokcancel(
                "Writes to the headset", f"{step.title}\n\n{step.help}\n\n{runner.display(argv)}\n\nContinue?",
                icon="warning"):
            return
        self._append(f"$ {runner.display(argv)}")
        self.status.config(text=f"running: {step.title}")
        self.run_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.running = True

        def started(proc):
            self.proc = proc

        def work():
            try:
                code = runner.run(argv, self.lines.put, started)
            except OSError as exc:
                self.lines.put(f"error: {exc}")
                code = -1
            self.lines.put(f"\x00{step.title}\x00{code}")

        threading.Thread(target=work, daemon=True).start()

    def _stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def _drain(self):
        try:
            while True:
                line = self.lines.get_nowait()
                if line.startswith("\x00"):
                    _, title, code = line.split("\x00")
                    self.proc, self.running = None, False
                    self.run_button.config(state="normal")
                    self.stop_button.config(state="disabled")
                    self.status.config(text=f"{title}: {'done' if code == '0' else 'failed (exit ' + code + ')'}")
                    self._append(f"[exit {code}]")
                else:
                    self._append(line)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _append(self, line: str):
        self.log.config(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.config(state="disabled")


def main(registry: Registry, headset: Optional[Headset] = None) -> int:
    if not registry.headsets:
        print("error: no headsets in devices/", file=sys.stderr)
        return 2
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"error: no display for the GUI ({exc}); use the command line", file=sys.stderr)
        return 2
    App(root, registry, headset)
    root.mainloop()
    return 0
