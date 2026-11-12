# SPDX-License-Identifier: Unlicense
"""The build container: the pinned image, the folders it sees, a worktree's repository.

The image's own scripts and their tests are in overpressure/builder.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from overpressure import linux


class ImageTests(unittest.TestCase):
    def test_the_image_is_the_builders_release_by_tag_and_digest(self):
        self.assertRegex(
            linux.IMAGE, r"^ghcr\.io/overpressure/builder:\d{4}\.\d{2}\.\d{2}(\.\d+)?@sha256:[0-9a-f]{64}$"
        )
        with mock.patch.dict(os.environ, {linux.IMAGE_OVERRIDE: ""}):
            self.assertEqual(linux.image(), linux.IMAGE)

    def test_another_image_for_working_on_the_builder(self):
        with mock.patch.dict(os.environ, {linux.IMAGE_OVERRIDE: "builder:local"}):
            self.assertEqual(linux.image(), "builder:local")
            self.assertIn("OVERPRESSURE_IMAGE=builder:local", linux.run_argv(["true"], tool="docker"))

    def test_pull_and_its_earlier_name_pull_the_pinned_image(self):
        for word in ("pull", "build"):
            with (
                mock.patch.object(linux, "engine", return_value="docker"),
                mock.patch.object(linux.subprocess, "call", return_value=0) as call,
                mock.patch.dict(os.environ, {linux.IMAGE_OVERRIDE: ""}),
            ):
                self.assertEqual(linux.main([word]), 0)
            call.assert_called_once_with(["docker", "pull", linux.IMAGE])


class LinkedFolderTests(unittest.TestCase):
    def test_a_worktrees_linked_private_folders_are_bound_where_they_are_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, other = Path(tmp, "worktree"), Path(tmp, "checkout")
            for folder in (root / "cache", other / "out", other / "assets"):
                folder.mkdir(parents=True)
            links = {str(root / "out"): str(other / "out"), str(root / "assets"): str(other / "assets")}
            real = os.path.realpath

            def realpath(path: os.PathLike[str] | str) -> str:
                return links.get(str(path), real(path))

            with mock.patch.object(linux.os.path, "realpath", realpath):
                mounts = linux.linked_mounts(root)
        # The worktree's own cache stays inside the repository's mount; a link to nothing is left.
        self.assertEqual(mounts, ["-v", f"{other / 'assets'}:/work/assets", "-v", f"{other / 'out'}:/work/out"])

    def test_links_inside_a_worktrees_own_private_folder_are_bound_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, other = Path(tmp, "worktree"), Path(tmp, "checkout")
            for folder in (root / "cache" / "stock", root / "cache" / "kernel", other / "cache" / "stock"):
                folder.mkdir(parents=True)
            (root / "cache" / "notes.txt").write_text("", encoding="utf-8")
            links = {str(root / "cache" / "stock"): str(other / "cache" / "stock")}
            real = os.path.realpath

            def realpath(path: os.PathLike[str] | str) -> str:
                return links.get(str(path), real(path))

            with mock.patch.object(linux.os.path, "realpath", realpath):
                mounts = linux.linked_mounts(root)
        # The worktree's own cache/kernel and its files stay inside the repository's mount.
        self.assertEqual(mounts, ["-v", f"{other / 'cache' / 'stock'}:/work/cache/stock"])


if __name__ == "__main__":
    unittest.main()


class WorktreeGitTests(unittest.TestCase):
    def test_a_worktrees_repository_is_bound_for_the_scripts_that_ask_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            main, root = Path(tmp, "checkout"), Path(tmp, "worktree")
            folder = main / ".git" / "worktrees" / "feature"
            folder.mkdir(parents=True)
            (folder / "commondir").write_text("../..\n", encoding="utf-8")
            root.mkdir()
            (root / ".git").write_text(f"gitdir: {folder.as_posix()}\n", encoding="utf-8")
            mounts, env = linux.worktree_git(root)
            common = os.path.realpath(main / ".git")
        self.assertEqual(mounts, ["-v", f"{common}:/git"])
        # GIT_DIR itself would reach the git of the builds' package recipes too.
        self.assertEqual(env, {"OVERPRESSURE_GIT_DIR": "/git/worktrees/feature"})

    def test_a_plain_checkout_needs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            self.assertEqual(linux.worktree_git(Path(tmp)), ([], {}))
            self.assertEqual(linux.worktree_git(Path(tmp, "nothing")), ([], {}))

    def test_the_container_run_carries_them(self):
        with mock.patch.object(
            linux, "worktree_git", return_value=(["-v", "C:/r/.git:/git"], {"OVERPRESSURE_GIT_DIR": "/git/worktrees/w"})
        ):
            argv = linux.run_argv(["true"], tool="docker")
        self.assertIn("OVERPRESSURE_GIT_DIR=/git/worktrees/w", argv)
        self.assertEqual(argv[argv.index("C:/r/.git:/git") - 1], "-v")
