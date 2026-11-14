import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import helpers  # noqa: F401  (isolated settings, script path)

import obsidian_note as on  # noqa: E402

RANGE = {"repo": "order-service", "from_ref": "origin/release", "from_kind": "branch", "from_sha": "a" * 40,
         "to_ref": "origin/staging", "to_kind": "branch", "to_sha": "b" * 40, "files": 12,
         "authors": ["dev one"],
         "counts": {"commits": 3, "endpoints": 2, "breaking": 1, "config_keys": 4, "migrations": 1, "entities": 1,
                    "dependencies": 0, "findings_high": 0}}


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="vault-")
        os.makedirs(os.path.join(self.vault, ".obsidian"))
        self.work = tempfile.mkdtemp(prefix="reports-")
        os.environ["OBSIDIAN_VAULT"] = self.vault
        os.environ.pop("BRANCH_COMPARE_OBSIDIAN_DIR", None)

    def tearDown(self):
        os.environ.pop("OBSIDIAN_VAULT", None)
        os.environ.pop("BRANCH_COMPARE_OBSIDIAN_DIR", None)
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)

    def report(self, repo="order-service", body="# Thay đổi\n\nnội dung\n"):
        stem = repo.replace("/", "_").replace(":", "_")
        md = os.path.join(self.work, f"{stem}.md")
        js = os.path.join(self.work, f"{stem}.json")
        with open(md, "w", encoding="utf-8") as fh:
            fh.write(body)
        with open(js, "w", encoding="utf-8") as fh:
            json.dump({"range": {**RANGE, "repo": repo}}, fh)
        return md, js

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()


class PublishTest(VaultCase):
    def test_note_in_the_repository_folder_named_by_stamp_and_refs(self):
        md, js = self.report(body="---\nold: front\n---\n# Thay đổi\n")
        path = on.publish(md, js, stamp="20261115-0142")
        self.assertEqual(path, os.path.join(self.vault, "code-changes", "order-service",
                                            "20261115-0142-release~staging.md"))
        text = self.read(path)
        self.assertTrue(text.startswith('---\ntype: "code-changes"\nrepo: "order-service"\nstamp: "20261115-0142"\n'))
        self.assertIn('from: "release"', text)
        self.assertIn('date: "2026-11-15"', text)
        self.assertIn("breaking: 1", text)
        self.assertIn('tags: ["code-changes", "service/order-service"]', text)
        self.assertIn(on.MARKER, text)
        self.assertNotIn("old: front", text)
        self.assertTrue(text.rstrip().endswith("# Thay đổi"))

    def test_default_stamp_is_now_and_a_bad_stamp_is_refused(self):
        md, js = self.report()
        name = os.path.basename(on.publish(md, js))
        self.assertRegex(name, r"^\d{8}-\d{4}-release~staging\.md$")
        with self.assertRaises(on.NoteError):
            on.publish(md, js, stamp="2026-11-15 01:42")
        with self.assertRaises(on.NoteError):
            on.publish(md, js, stamp="20261115-01:42")     # a colon breaks Windows and Android

    def test_rerun_overwrites_its_own_note_but_never_a_foreign_one(self):
        md, js = self.report()
        path = on.publish(md, js, stamp="20261115-0142")
        on.publish(md, js, stamp="20261115-0142")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# my own notes\n")
        with self.assertRaises(on.NoteError) as caught:
            on.publish(md, js, stamp="20261115-0142")
        self.assertEqual(caught.exception.code, 4)
        self.assertEqual(self.read(path), "# my own notes\n")

    def test_folder_setting_branch_slashes_and_unsafe_names_stay_inside_the_vault(self):
        os.environ["BRANCH_COMPARE_OBSIDIAN_DIR"] = "../outside/Releases"
        md, js = self.report(repo="svc:a/b")
        with open(js, encoding="utf-8") as fh:
            data = json.load(fh)
        data["range"]["to_ref"] = "origin/feature/x"
        with open(js, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        path = on.publish(md, js, stamp="20261115-0142")
        self.assertTrue(os.path.realpath(path).startswith(os.path.realpath(self.vault) + os.sep))
        self.assertIn(os.path.join("outside", "Releases", "svc-a-b"), path)
        self.assertEqual(os.path.basename(path), "20261115-0142-release~feature-x.md")

    def test_missing_vault_setting_is_exit_3(self):
        os.environ.pop("OBSIDIAN_VAULT")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(on.main(["check"]), 3)
        self.assertIn("OBSIDIAN_VAULT", err.getvalue())


class IndexTest(VaultCase):
    def test_summary_note_links_every_service_note_of_the_stamp(self):
        for repo in ("order-service", "pricing"):
            on.publish(*self.report(repo), stamp="20261115-0142")
        on.publish(*self.report("other"), stamp="20261115-0200")
        summary = os.path.join(self.work, "summary.md")
        with open(summary, "w", encoding="utf-8") as fh:
            fh.write("> [!summary] Đợt này\n")
        path = on.index("20261115-0142", summary)
        text = self.read(path)
        self.assertEqual(path, os.path.join(self.vault, "code-changes", "_tong-hop", "20261115-0142-release~staging.md"))
        self.assertIn("[[code-changes/order-service/20261115-0142-release~staging\\|order-service]]", text)
        self.assertIn("[[code-changes/pricing/20261115-0142-release~staging\\|pricing]]", text)
        self.assertNotIn("other", text)
        self.assertIn("> [!summary] Đợt này", text)
        self.assertIn('stamp = "20261115-0142"', text)
        self.assertIn("services: 2", text)

    def test_notes_not_written_by_the_skill_are_left_out(self):
        on.publish(*self.report(), stamp="20261115-0142")
        with open(os.path.join(self.vault, "code-changes", "order-service", "20261115-0142-hand.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# hand\n")
        self.assertNotIn("hand", self.read(on.index("20261115-0142")))

    def test_stamp_without_notes_is_an_error(self):
        with self.assertRaises(on.NoteError):
            on.index("20261115-2359")


class SyncTest(VaultCase):
    """sync drives Obsidian's CLI; a fake CLI stands in for the running app."""

    def setUp(self):
        super().setUp()
        import subprocess
        self.git = lambda *a: subprocess.run(["git", "-C", self.vault, *a], check=True, capture_output=True)
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "start")
        self.bin = tempfile.mkdtemp(prefix="fake-cli-")
        self.log = os.path.join(self.bin, "calls")
        os.environ["OBSIDIAN_CLI"] = os.path.join(self.bin, "obsidian")

    def tearDown(self):
        for name in ("OBSIDIAN_CLI", "BRANCH_COMPARE_OBSIDIAN_SYNC"):
            os.environ.pop(name, None)
        shutil.rmtree(self.bin, ignore_errors=True)
        super().tearDown()

    def fake_cli(self, body):
        path = os.environ["OBSIDIAN_CLI"]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\necho "$@" >> "{self.log}"\n{body}\n')
        os.chmod(path, 0o755)

    def test_runs_the_sync_command_and_waits_for_the_commit(self):
        on.publish(*self.report(), stamp="20261115-0142")
        self.fake_cli(f'git -C "{self.vault}" add -A && git -C "{self.vault}" commit -q -m sync\necho "Executed: $2"')
        result = on.sync(timeout=5, poll=0.1)
        self.assertEqual(result["status"], "synced")
        self.assertEqual(self.read(self.log).strip(),
                         f"command id=git-vault-sync:sync vault={os.path.basename(self.vault)}")

    def test_waits_for_the_push_that_follows_the_commit(self):
        import subprocess
        remote = tempfile.mkdtemp(prefix="remote-")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        self.git("remote", "add", "origin", remote)
        self.git("push", "-q", "origin", "main")
        on.publish(*self.report(), stamp="20261115-0142")
        # Commit now, push a moment later in the background, the way the plugin does.
        self.fake_cli(f'git -C "{self.vault}" add -A && git -C "{self.vault}" commit -q -m sync\n'
                      f'(sleep 0.5; git -C "{self.vault}" push -q origin main) >/dev/null 2>&1 &\n'
                      'echo "Executed: $2"')
        result = on.sync(timeout=10, poll=0.1)
        self.assertIn("đã đẩy lên remote", result["detail"])
        shutil.rmtree(remote, ignore_errors=True)

    def test_obsidian_not_running_is_exit_5_and_keeps_the_notes(self):
        path = on.publish(*self.report(), stamp="20261115-0142")
        self.fake_cli('echo "The CLI is unable to find Obsidian. Please make sure Obsidian is running." >&2; exit 1')
        with self.assertRaises(on.NoteError) as caught:
            on.sync(timeout=1, poll=0.1)
        self.assertEqual(caught.exception.code, 5)
        self.assertTrue(os.path.exists(path))

    def test_started_but_never_committed_is_exit_6(self):
        on.publish(*self.report(), stamp="20261115-0142")
        self.fake_cli('echo "Executed: $2"')
        with self.assertRaises(on.NoteError) as caught:
            on.sync(timeout=0.3, poll=0.1)
        self.assertEqual(caught.exception.code, 6)

    def test_custom_command_and_switch_off(self):
        on.publish(*self.report(), stamp="20261115-0142")
        os.environ["BRANCH_COMPARE_OBSIDIAN_SYNC"] = "off"
        self.assertEqual(on.sync()["status"], "off")
        self.assertFalse(os.path.exists(self.log))

    def test_missing_cli_is_exit_5(self):
        with self.assertRaises(on.NoteError) as caught:
            on.sync()
        self.assertEqual(caught.exception.code, 5)
