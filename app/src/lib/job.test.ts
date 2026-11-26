// SPDX-License-Identifier: Unlicense
import { describe, expect, it } from "vitest";
import { JOB_LINES, applyJob, jobLine, newJob } from "./job";

describe("a job's state", () => {
  it("follows its lines, its progress and its end", () => {
    const job = newJob("job-1", "download", "phoenix");
    expect(jobLine(job)).toBe("Starting…");
    applyJob(job, { job: "job-1", event: "job-line", line: "downloading" });
    applyJob(job, { job: "job-1", event: "job-progress", fraction: 0.5, text: "20 of 40 MB" });
    expect(jobLine(job)).toBe("20 of 40 MB");
    applyJob(job, { job: "job-1", event: "job-line", line: "checked" });
    applyJob(job, { job: "job-1", event: "job-ended", kind: "download", code: 0, error: "" });
    expect(job.fraction).toBe(1);
    expect(jobLine(job)).toBe("checked");
  });

  it("says why it failed: the error, else its last line", () => {
    const job = newJob("job-2", "export", "phoenix");
    applyJob(job, { job: "job-2", event: "job-line", line: "the disk is full" });
    applyJob(job, { job: "job-2", event: "job-ended", kind: "export", code: 1, error: "" });
    expect(jobLine(job)).toBe("Failed: the disk is full");
  });

  it("ignores another job's events and keeps the last lines", () => {
    const job = newJob("job-3", "step", "phoenix");
    applyJob(job, { job: "job-4", event: "job-line", line: "other" });
    for (let n = 0; n < JOB_LINES + 5; n++) applyJob(job, { job: "job-3", event: "job-line", line: `l${n}` });
    expect(job.lines).toHaveLength(JOB_LINES);
    expect(job.lines[0]).toBe("l5");
  });
});
