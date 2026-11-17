import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import plugin_settings  # noqa: E402
import vault  # noqa: E402


def git(root, *args):
    subprocess.run(["git", "-C", root, *args], check=True, capture_output=True, text=True)


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(os.path.join(self.tmp.name, "my-vault"))
        os.makedirs(os.path.join(self.root, ".obsidian", "plugins", "git-vault-sync"))
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        env = {k: v for k, v in os.environ.items() if not k.startswith("OBSIDIAN_")}
        env.update({"OBSIDIAN_VAULT": self.root, "DEV_CLI_HOME": self.home,
                    "OBSIDIAN_BACKUP_DIR": os.path.join(self.tmp.name, "backups")})
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        plugin_settings._SETTINGS = None
        self.note("Welcome.md", "# Welcome\n")
        self.note("work/Meeting.md", textwrap.dedent("""\
            ---
            tags:
              - meeting
            status: open
            ---
            # Meeting

            ## Agenda
            - item one

            ## Notes
            Text about #project/alpha here.
            """))
        self.note("other/Meeting.md", "# Another meeting\n")
        self.note("work/Plan.md", "Links to [[Meeting]] and [[Missing note]].\n")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def note(self, rel, text):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def text(self, rel):
        with open(os.path.join(self.root, *rel.split("/")), encoding="utf-8") as fh:
            return fh.read()

    def run_cli(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin or "")):
            code = vault.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    # -- resolve and read

    def test_resolve_by_path_and_name(self):
        self.assertTrue(vault.resolve(self.root, "work/Meeting").endswith("work/Meeting.md"))
        self.assertTrue(vault.resolve(self.root, "Welcome").endswith("Welcome.md"))
        self.assertTrue(vault.resolve(self.root, "[[Plan|the plan]]").endswith("work/Plan.md"))
        with self.assertRaises(vault.VaultError) as ctx:
            vault.resolve(self.root, "Meeting")
        self.assertEqual(ctx.exception.code, 4)
        self.assertIn("other/Meeting.md", str(ctx.exception))

    def test_system_folders_are_never_read(self):
        self.note(".obsidian/plugins/git-vault-sync/data.json", '{"token": "secret"}')
        code, out, err = self.run_cli("read", ".obsidian/plugins/git-vault-sync/data.json")
        self.assertEqual(code, 2)
        self.assertNotIn("secret", out + err)
        code, out, _ = self.run_cli("search", "secret")
        self.assertEqual(code, 4)
        self.assertNotIn("secret\"", out)

    def test_read_section_and_props(self):
        code, out, _ = self.run_cli("read", "work/Meeting", "--section", "agenda")
        self.assertEqual(code, 0)
        self.assertIn("- item one", out)
        self.assertNotIn("Text about", out)
        code, out, _ = self.run_cli("read", "work/Meeting", "--props")
        props = json.loads(out.split("\n", 1)[1])
        self.assertEqual(props, {"tags": ["meeting"], "status": "open"})

    def test_search_and_filters(self):
        code, out, _ = self.run_cli("search", "about")
        self.assertEqual(code, 0)
        self.assertIn("work/Meeting.md", out)
        code, out, _ = self.run_cli("search", "meeting", "--tag", "project")
        self.assertIn("work/Meeting.md", out)
        self.assertNotIn("other/Meeting.md", out)
        code, out, _ = self.run_cli("list", "--prop", "status=open")
        self.assertIn("work/Meeting.md", out)
        self.assertNotIn("Welcome", out)

    def test_links_and_backlinks(self):
        code, out, _ = self.run_cli("links", "work/Plan")
        self.assertIn("Missing note  (chưa có ghi chú)", out)
        code, out, _ = self.run_cli("links", "Welcome")
        self.assertIn("liên kết đến (0)", out)

    # -- write

    def test_write_never_overwrites_without_flag(self):
        code, out, _ = self.run_cli("write", "work/New note", "--stdin", "--prop", "status=draft", stdin="# New\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.text("work/New note.md"), "---\nstatus: draft\n---\n# New\n")
        code, _, err = self.run_cli("write", "work/New note", "--stdin", stdin="other")
        self.assertEqual(code, 4)
        self.assertIn("không ghi đè", err)
        code, out, _ = self.run_cli("write", "work/New note", "--stdin", "--overwrite", stdin="replaced")
        self.assertEqual(code, 0)
        self.assertEqual(self.text("work/New note.md"), "replaced\n")
        backups = [os.path.join(d, f) for d, _, fs in os.walk(os.path.join(self.tmp.name, "backups")) for f in fs]
        self.assertEqual(len(backups), 1)

    def test_write_rejects_bad_names_and_escapes(self):
        for name in ("../outside", ".obsidian/x", "a/.hidden", "bad:name", "q?", ".git/config"):
            code, _, _ = self.run_cli("write", name, "--stdin", stdin="x")
            self.assertEqual(code, 2, name)
        os.symlink(self.tmp.name, os.path.join(self.root, "link"))
        code, _, err = self.run_cli("write", "link/escaped", "--stdin", stdin="x")
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escaped.md")))

    def test_inbox_for_bare_names(self):
        os.environ["OBSIDIAN_INBOX"] = "Inbox"
        plugin_settings._SETTINGS = None
        code, out, _ = self.run_cli("write", "Idea", "--stdin", stdin="text")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Inbox", "Idea.md")))

    def test_append_to_section_keeps_lists_and_next_heading(self):
        code, _, _ = self.run_cli("append", "work/Meeting", "--section", "Agenda", "--text", "- item two")
        self.assertEqual(code, 0)
        self.assertIn("- item one\n- item two\n\n## Notes", self.text("work/Meeting.md"))
        code, _, _ = self.run_cli("append", "work/Meeting", "--text", "Closing line.")
        self.assertTrue(self.text("work/Meeting.md").endswith("here.\n\nClosing line.\n"))
        code, _, err = self.run_cli("append", "work/Meeting", "--section", "Nope", "--text", "x")
        self.assertEqual(code, 4)
        code, _, _ = self.run_cli("append", "work/Meeting", "--section", "Actions", "--text", "- [ ] call", "--create")
        self.assertIn("## Actions\n\n- [ ] call\n", self.text("work/Meeting.md"))

    def test_auto_sync_hint(self):
        code, out, _ = self.run_cli("append", "Welcome", "--text", "x")
        self.assertNotIn("auto-sync", out)
        os.environ["OBSIDIAN_AUTO_SYNC"] = "on"
        plugin_settings._SETTINGS = None
        code, out, _ = self.run_cli("append", "Welcome", "--text", "y")
        self.assertIn("auto-sync: on", out)

    def test_append_create_missing_note(self):
        code, _, _ = self.run_cli("append", "journal/2026-11-17", "--text", "first", "--create")
        self.assertEqual(code, 0)
        self.assertEqual(self.text("journal/2026-11-17.md"), "first\n")
        code, _, _ = self.run_cli("append", "journal/nothing", "--text", "x")
        self.assertEqual(code, 4)

    def test_prop_edits_keep_other_lines(self):
        code, out, _ = self.run_cli("prop", "work/Meeting", "--set", "status=done", "--add", "tags=review",
                                    "--set", "title=A: B", "--unset", "missing")
        self.assertEqual(code, 0)
        text = self.text("work/Meeting.md")
        self.assertIn("tags:\n  - meeting\n  - review\nstatus: done\ntitle: \"A: B\"\n---\n# Meeting", text)
        self.run_cli("prop", "Welcome", "--list", "aliases=Home,Start")
        self.assertTrue(self.text("Welcome.md").startswith("---\naliases:\n  - Home\n  - Start\n---\n# Welcome"))
        self.run_cli("prop", "Welcome", "--unset", "aliases")
        self.assertEqual(self.text("Welcome.md"), "# Welcome\n")

    def test_headings_in_code_fences_are_ignored(self):
        self.note("code.md", "## Real\n```\n## fake\n```\nend\n")
        start, end = vault.find_section(self.text("code.md").splitlines(), "Real")
        self.assertEqual((start, end), (0, 5))

    def test_moment_format(self):
        import datetime
        day = datetime.date(2026, 3, 7)
        self.assertEqual(vault.moment_format("YYYY-MM-DD", day), "2026-03-07")
        self.assertEqual(vault.moment_format("[Nhật ký] YYYY/M/D", day), "Nhật ký 2026/3/7")

    # -- sync

    def make_git_vault(self):
        remote = os.path.join(self.tmp.name, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "t")
        with open(os.path.join(self.root, ".obsidian", "plugins", "git-vault-sync", "data.json"), "w") as fh:
            json.dump({"excludePaths": ".obsidian/workspace.json\n.obsidian/plugins/git-vault-sync/data.json",
                       "token": "secret-token"}, fh)
        git(self.root, "add", "--", "Welcome.md", "work", "other")
        git(self.root, "commit", "-q", "-m", "init")
        git(self.root, "remote", "add", "origin", remote)
        git(self.root, "push", "-q", "-u", "origin", "main")

    def fake_cli(self, body):
        path = os.path.join(self.tmp.name, "obsidian")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n" + body + "\n")
        os.chmod(path, 0o755)
        os.environ["OBSIDIAN_CLI"] = path
        plugin_settings._SETTINGS = None
        return path

    def test_sync_waits_for_commit_and_push(self):
        self.make_git_vault()
        self.note("work/Plan.md", "changed\n")
        self.note(".obsidian/workspace.json", "{}")
        r = self.root
        self.fake_cli(f'echo "$@" > "{self.tmp.name}/args"\n'
                      f'git -C "{r}" add -- work >/dev/null && git -C "{r}" commit -qm sync && '
                      f'git -C "{r}" push -q origin main 2>/dev/null\necho "Executed: git-vault-sync:sync"')
        code, out, err = self.run_cli("sync", "--timeout", "5")
        self.assertEqual(code, 0, err)
        self.assertIn("synced: commit mới", out)
        self.assertIn("đã đẩy lên remote", out)
        with open(os.path.join(self.tmp.name, "args")) as fh:
            self.assertEqual(fh.read().split(), ["command", "id=git-vault-sync:sync", "vault=my-vault"])

    def test_sync_times_out_when_nothing_is_committed(self):
        self.make_git_vault()
        self.note("work/Plan.md", "changed\n")
        self.fake_cli('echo "Executed: git-vault-sync:sync"')
        with mock.patch.object(vault.time, "sleep"):
            code, _, err = self.run_cli("sync", "--timeout", "0")
        self.assertEqual(code, 6)
        self.assertIn("work/Plan.md", err)

    def test_sync_reports_obsidian_not_running(self):
        self.make_git_vault()
        self.fake_cli('echo "Error: unable to find Obsidian. Is it running?"; exit 1')
        code, _, err = self.run_cli("sync")
        self.assertEqual(code, 5)

    def test_sync_refuses_with_conflicts(self):
        self.make_git_vault()
        with mock.patch.object(vault, "conflicts", return_value=["work/Plan.md"]):
            self.fake_cli('touch "%s/ran"; echo Executed' % self.tmp.name)
            code, _, err = self.run_cli("sync")
        self.assertEqual(code, 7)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "ran")))

    def test_status_and_doctor_never_print_the_token(self):
        self.make_git_vault()
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("thay đổi chưa commit (trừ excludePaths của plugin): 0", out)
        code, out, _ = self.run_cli("doctor")
        self.assertIn("chưa nằm trong .gitignore", out)
        self.assertNotIn("secret-token", out)


if __name__ == "__main__":
    unittest.main()
