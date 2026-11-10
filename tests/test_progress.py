# SPDX-License-Identifier: Unlicense
"""Progress lines, estimates in words, the byte meter and the pipe that counts what a child reads."""

from __future__ import annotations

import io
import os
import sys
import threading
import unittest

from overpressure import process, progress, qualcomm


class LineTests(unittest.TestCase):
    def test_lines_round_trip(self):
        self.assertEqual(progress.parse(progress.line("writing super", 0.425)), (0.425, "writing super"))
        self.assertEqual(progress.parse(progress.line("reading the table")), (None, "reading the table"))

    def test_the_three_forms(self):
        self.assertEqual(progress.parse("progress 42%: writing super"), (0.42, "writing super"))
        self.assertEqual(progress.parse("progress 3/8: package pico4-monado"), (0.375, "package pico4-monado"))
        self.assertEqual(progress.parse("progress: reading aop"), (None, "reading aop"))

    def test_other_lines_are_none(self):
        for text in ("writing super", "progress is slow", "my progress 3/8: x", ">> [  12s] building"):
            with self.subTest(text=text):
                self.assertIsNone(progress.parse(text))

    def test_fractions_stay_between_zero_and_one(self):
        self.assertEqual(progress.parse("progress 120%: x"), (1.0, "x"))
        self.assertEqual(progress.parse("progress 9/0: x"), (1.0, "x"))
        self.assertEqual(progress.line("x", 1.5), "progress 100.0%: x")


class EstimateTests(unittest.TestCase):
    def test_estimates_in_words(self):
        cases = {
            "about 20 min": 1200,
            "about 1 min": 60,
            "1 to 3 h": 3 * 3600,
            "about 1 h": 3600,
            "a few seconds": 10,
            "a few minutes the first time": 300,
            "under a minute": 60,
            "a minute": 60,
            "about 30 s": 30,
        }
        for words, seconds in cases.items():
            with self.subTest(words=words):
                self.assertEqual(progress.estimate_seconds(words), seconds)

    def test_no_estimate(self):
        for words in ("", "optional", "minutes to hours", "check"):
            with self.subTest(words=words):
                self.assertIsNone(progress.estimate_seconds(words))

    def test_clock_and_duration(self):
        self.assertEqual(progress.clock(5), "0:05")
        self.assertEqual(progress.clock(754), "12:34")
        self.assertEqual(progress.clock(3723), "1:02:03")
        self.assertEqual(progress.duration(20), "under a minute")
        self.assertEqual(progress.duration(250), "4 min")
        self.assertEqual(progress.duration(4800), "1 h 20 min")
        self.assertEqual(progress.duration(7200), "2 h")


class MeterTests(unittest.TestCase):
    def test_one_line_per_percent_and_the_last(self):
        lines: list[str] = []
        meter = progress.Meter(1000, lambda done, total: f"{done} of {total}", out=lines.append)
        for _ in range(100):
            meter.add(10)
        self.assertEqual(len(lines), 100)
        self.assertEqual(lines[-1], "progress 100.0%: 1000 of 1000")
        self.assertEqual(progress.parse(lines[0]), (0.01, "10 of 1000"))

    def test_a_meter_for_part_of_a_task(self):
        lines: list[str] = []
        meter = progress.Meter(100, lambda _d, _t: "writes", end=0.9, out=lines.append)
        meter.add(50)
        meter.add(50)
        self.assertEqual([progress.parse(line) for line in lines], [(0.45, "writes"), (0.9, "writes")])

    def test_mib(self):
        self.assertEqual(progress.mib(1200 << 20, 2900 << 20), "1200 of 2900 MiB")


class FeedTests(unittest.TestCase):
    def test_a_child_reads_everything_and_the_meter_counts_it(self):
        data = os.urandom(3 * progress.CHUNK + 17)
        lines: list[str] = []
        meter = progress.Meter(len(data), progress.mib, out=lines.append)
        script = "import sys; data = sys.stdin.buffer.read(); print(len(data))"
        with progress.feed(io.BytesIO(data), meter) as pipe:
            output = process.run([sys.executable, "-c", script], 60, stdin=pipe)
        self.assertEqual(output, str(len(data)))
        self.assertEqual(meter.done, len(data))
        self.assertEqual(progress.parse(lines[-1]), (1.0, "3 of 3 MiB"))

    def test_a_child_that_stops_reading_ends_the_feed(self):
        meter = progress.Meter(1 << 30, lambda _d, _t: "x", out=lambda _line: None)
        before = threading.active_count()
        # Far more than a pipe holds: the feed blocks until the child has ended and the pipe is closed.
        with progress.feed(io.BytesIO(bytes(64 << 20)), meter) as pipe:
            output = process.run([sys.executable, "-c", "print('done')"], 60, stdin=pipe)
        self.assertEqual(output, "done")
        self.assertLessEqual(threading.active_count(), before)


class StreamedTests(unittest.TestCase):
    def test_lines_arrive_as_they_come_carriage_returns_included(self):
        seen: list[str] = []
        script = "import sys; sys.stdout.write('a: 1%\\ra: 50%\\rdone\\nlast'); sys.stdout.flush()"
        output = process.run_streamed([sys.executable, "-c", script], 60, seen.append)
        self.assertEqual(seen, ["a: 1%", "a: 50%", "done", "last"])
        self.assertIn("last", output)

    def test_a_failure_raises(self):
        with self.assertRaises(RuntimeError):
            process.run_streamed([sys.executable, "-c", "import sys; sys.exit(3)"], 60, lambda _line: None)


class DumpProgressTests(unittest.TestCase):
    def test_each_partition_and_each_tenth(self):
        lines: list[str] = []
        dump = qualcomm.DumpProgress(lines.append)
        for text in (
            "13:53:10.489 [INFO]  Found 60 partitions on LUN 4.",
            "Dumping aop: 6.2% (0.03 / 0.50 MiB) [N/A]",
            "Dumping aop: 8.2% (0.04 / 0.50 MiB) [N/A]",
            "Dumping aop: 12.5% (0.06 / 0.50 MiB) [N/A]",
            "Dumping super: 1.0% (30 / 3000 MiB) [N/A]",
        ):
            dump(text)
        self.assertEqual(lines, ["reading aop", "reading aop (10%)", "reading super"])


if __name__ == "__main__":
    unittest.main()
