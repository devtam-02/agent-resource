"""Shared test helpers: isolated settings, throw-away git repositories."""

import os
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills", "review-pr", "scripts"))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# Settings come from an empty dev-cli home, never from the developer's ~/.dev-cli/.env.
SETTINGS_HOME = tempfile.mkdtemp(prefix="spr-settings-")
os.environ["DEV_CLI_HOME"] = SETTINGS_HOME
for name in list(os.environ):
    if name.startswith(("BITBUCKET_", "SPRING_PR_REVIEW_")):
        del os.environ[name]
# Git must not read the developer's global configuration (hooks, signing, templates).
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
os.environ["GIT_AUTHOR_NAME"] = os.environ["GIT_COMMITTER_NAME"] = "Tester"
os.environ["GIT_AUTHOR_EMAIL"] = os.environ["GIT_COMMITTER_EMAIL"] = "tester@example.invalid"


class Repo:
    def __init__(self, branch="main", name="service"):
        self.parent = tempfile.mkdtemp(prefix="spr-repo-")
        self.path = os.path.join(self.parent, name)
        os.makedirs(self.path)
        self.git("init", "-q", "-b", branch)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.path, *args], check=True, capture_output=True, text=True).stdout

    def write(self, rel, text):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def append(self, rel, text):
        with open(os.path.join(self.path, rel), "a", encoding="utf-8") as fh:
            fh.write(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def branch(self, name):
        self.git("checkout", "-q", "-b", name)

    def checkout(self, name):
        self.git("checkout", "-q", name)

    def cleanup(self):
        shutil.rmtree(self.parent, ignore_errors=True)
