// SPDX-License-Identifier: Unlicense
import { describe, expect, it } from "vitest";
import type { RunEvent } from "./protocol";
import { LOG_LINES, apply, duration, initial, overall, runLeft, stageStatus } from "./run";

const RUN = "run-1";

function plan(): RunEvent {
  return {
    run: RUN,
    event: "plan",
    headset: { codename: "phoenix", name: "PICO 4" },
    workflow: "install",
    stages: [
      { id: "a", title: "A", summary: "", tasks: [{ id: "a1", title: "A1", estimate: "", seconds: null, writes: false }] },
      {
        id: "b",
        title: "B",
        summary: "",
        tasks: [
          { id: "b1", title: "B1", estimate: "", seconds: null, writes: true },
          { id: "b2", title: "B2", estimate: "", seconds: null, writes: false },
        ],
      },
    ],
  };
}

describe("a run's state", () => {
  it("takes the plan and starts at nothing done", () => {
    const state = initial(RUN);
    apply(state, plan());
    expect(state.headset).toBe("PICO 4");
    expect(overall(state)).toMatchObject({ fraction: 0, stage: 0 });
    expect(stageStatus(state.stages[0]!)).toBe("pending");
  });

  it("counts each stage as an equal part, a running task by its reported fraction", () => {
    const state = initial(RUN);
    apply(state, plan());
    apply(state, { run: RUN, event: "task", stage: "a", task: "a1", status: "done" });
    apply(state, { run: RUN, event: "task", stage: "b", task: "b1", status: "running" });
    apply(state, { run: RUN, event: "progress", task: "b1", fraction: 0.5, text: "half" });
    const now = overall(state);
    expect(now.fraction).toBeCloseTo(0.5 + 0.5 * 0.25);
    expect(now.stage).toBe(1);
    expect(now.task?.id).toBe("b1");
    expect(stageStatus(state.stages[1]!)).toBe("active");
  });

  it("does not count a running task without a fraction", () => {
    const state = initial(RUN);
    apply(state, plan());
    apply(state, { run: RUN, event: "task", stage: "a", task: "a1", status: "running" });
    apply(state, { run: RUN, event: "progress", task: "a1", fraction: null, text: "part 3" });
    expect(overall(state).fraction).toBe(0);
    expect(overall(state).task?.progress).toBe("part 3");
  });

  it("ignores another run's events and keeps the last log lines", () => {
    const state = initial(RUN);
    apply(state, { run: "run-2", event: "log", task: "x", line: "other" });
    for (let n = 0; n < LOG_LINES + 10; n++) apply(state, { run: RUN, event: "log", task: "x", line: `l${n}` });
    expect(state.log).toHaveLength(LOG_LINES);
    expect(state.log[0]).toBe("l10");
  });

  it("clears the ask when the run finishes", () => {
    const state = initial(RUN);
    apply(state, {
      run: RUN,
      event: "ask",
      id: "x",
      kind: "confirm",
      title: "T",
      text: "",
      accept: "Yes",
      decline: "No",
    });
    apply(state, { run: RUN, event: "finished", status: "stopped", task: null });
    expect(state.ask).toBeNull();
    expect(state.finished?.status).toBe("stopped");
  });
});

describe("the time a run has left", () => {
  it("adds the estimates of what is not done, a running task by what of it is left", () => {
    const state = initial(RUN);
    const timed = plan();
    if (timed.event !== "plan") throw new Error("a plan");
    timed.stages[0]!.tasks[0]!.seconds = 120;
    timed.stages[1]!.tasks[0]!.seconds = 600;
    timed.stages[1]!.tasks[1]!.seconds = 60;
    apply(state, timed);
    expect(runLeft(state)).toEqual({ seconds: 780, atLeast: false });
    apply(state, { run: RUN, event: "task", stage: "a", task: "a1", status: "done" });
    apply(state, { run: RUN, event: "task", stage: "b", task: "b1", status: "running" });
    apply(state, { run: RUN, event: "progress", task: "b1", fraction: 0.5, text: "writing" });
    expect(runLeft(state)).toEqual({ seconds: 360, atLeast: false });
    expect(duration(runLeft(state))).toBe("about 6 min");
  });

  it("says when a task without an estimate makes it longer, and nothing when nothing is known", () => {
    const state = initial(RUN);
    apply(state, plan());
    expect(duration(runLeft(state))).toBe("");
    expect(duration({ seconds: 30, atLeast: false })).toBe("under a minute");
    expect(duration({ seconds: 300, atLeast: true })).toBe("at least 5 min");
    expect(duration({ seconds: 4800, atLeast: false })).toBe("about 1 h 20 min");
    expect(duration({ seconds: 3600, atLeast: false })).toBe("about 1 h");
  });
});
