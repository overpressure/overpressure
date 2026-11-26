// SPDX-License-Identifier: Unlicense
// A job as its events tell it: a download, the backup's export or import, the logs, a step of Advanced mode.
import type { JobEvent, JobKind } from "./protocol";

/** A job's output keeps the last lines only. */
export const JOB_LINES = 5000;

export interface JobState {
  id: string;
  kind: JobKind;
  /** The headset it is for. */
  codename: string;
  lines: string[];
  /** The last progress line's share, 0 to 1; null while unknown. */
  fraction: number | null;
  progress: string;
  /** Its exit status once it has ended; null while it runs. */
  code: number | null;
  error: string;
}

export function newJob(id: string, kind: JobKind, codename: string): JobState {
  return { id, kind, codename, lines: [], fraction: null, progress: "", code: null, error: "" };
}

/** Apply one of the job's events to JOB (in place); another job's events are ignored. */
export function applyJob(job: JobState, event: JobEvent): void {
  if (event.job !== job.id) return;
  switch (event.event) {
    case "job-line":
      job.lines.push(event.line);
      if (job.lines.length > JOB_LINES) job.lines.splice(0, job.lines.length - JOB_LINES);
      break;
    case "job-progress":
      job.fraction = event.fraction;
      job.progress = event.text;
      break;
    case "job-ended":
      job.code = event.code;
      job.error = event.code === 0 ? "" : event.error || job.lines.at(-1) || `It ended with status ${event.code}.`;
      if (event.code === 0) job.fraction = 1;
      break;
  }
}

/** The job's last line in words, for a one-line status under its button. */
export function jobLine(job: JobState): string {
  if (job.code !== null) return job.code === 0 ? (job.lines.at(-1) ?? "Done.") : `Failed: ${job.error}`;
  return job.progress || job.lines.at(-1) || "Starting…";
}
