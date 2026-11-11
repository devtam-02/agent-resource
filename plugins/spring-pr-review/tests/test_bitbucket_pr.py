import contextlib
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import helpers  # noqa: F401  (isolated settings, script path)
from helpers import Repo

import bitbucket_pr  # noqa: E402

TOKEN = "tok-THIS-MUST-NOT-LEAK-123456"
PR_PATH = "/rest/api/1.0/projects/SHOP/repos/order-service/pull-requests/13"
HEAD = "11b4dc8a" + "0" * 32


class Stub(BaseHTTPRequestHandler):
    routes = {}
    seen_auth = []

    def do_GET(self):  # noqa: N802
        Stub.seen_auth.append(self.headers.get("Authorization"))
        path = self.path.split("?")[0]
        if path not in Stub.routes:
            self.send_response(404)
            self.end_headers()
            return
        status, body = Stub.routes[path]
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass


def pr_body():
    return {
        "id": 13, "title": "Merge sync-phase2 to staging", "state": "OPEN",
        "description": "Sync.\ndb password=SuperSecret99 must stay\nAuthorization: Bearer abcdefghijkl",
        "author": {"user": {"displayName": "Dev One"}},
        "createdDate": 1790000000000, "updatedDate": 1790100000000,
        "fromRef": {"displayId": "sync-phase2", "latestCommit": HEAD},
        "toRef": {"displayId": "staging", "latestCommit": "6d93f381" + "0" * 32},
        "reviewers": [{"user": {"displayName": "Rev"}, "status": "UNAPPROVED"}],
        "links": {"self": [{"href": "https://bitbucket.example/projects/SHOP/repos/x/pull-requests/13"}]},
    }


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Stub)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Stub.routes = {
            PR_PATH: (200, pr_body()),
            PR_PATH + "/merge": (200, {"canMerge": False, "conflicted": False, "outcome": "CLEAN",
                                       "vetoes": [{"summaryMessage": "Needs 1 approval"}]}),
            PR_PATH + "/commits": (200, {"size": 17, "isLastPage": True, "values": []}),
            f"/rest/build-status/1.0/commits/{HEAD}": (200, {"values": []}),
            "/rest/api/1.0/projects/SHOP/repos/order-service/pull-requests":
                (200, {"values": [
                    {"id": 13, "title": "to staging", "fromRef": {"displayId": "sync-phase2"},
                     "toRef": {"displayId": "staging"}, "links": {"self": [{"href": "u13"}]}},
                    {"id": 14, "title": "to release", "fromRef": {"displayId": "sync-phase2"},
                     "toRef": {"displayId": "release"}, "links": {"self": [{"href": "u14"}]}}]}),
            "/rest/api/1.0/application-properties": (200, {"version": "8.9"}),
            "/rest/api/1.0/projects": (200, {"values": []}),
        }
        Stub.seen_auth = []
        os.environ["BITBUCKET_URL"] = self.base
        os.environ["BITBUCKET_TOKEN"] = TOKEN

    def tearDown(self):
        for name in ("BITBUCKET_URL", "BITBUCKET_TOKEN", "BITBUCKET_USER", "BITBUCKET_PASSWORD"):
            os.environ.pop(name, None)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bitbucket_pr.main(argv)
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def pr_url(self):
        return f"{self.base}/projects/SHOP/repos/order-service/pull-requests/13/overview"


class InfoTest(ServerCase):
    def test_info_from_url(self):
        code, out, _ = self.run_main(["info", self.pr_url(), "--repo", "/nonexistent"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        pr = data["pr"]
        self.assertEqual((pr["id"], pr["from"]["branch"], pr["to"]["branch"]), (13, "sync-phase2", "staging"))
        self.assertEqual(pr["commit_count"], "17")
        self.assertEqual(pr["author"], "Dev One")
        self.assertNotIn("SuperSecret99", out)
        self.assertNotIn("abcdefghijkl", out)
        self.assertEqual(data["merge"]["vetoes"], ["Needs 1 approval"])
        self.assertEqual(data["builds"]["summary"], "không có build status nào")
        self.assertTrue(all(a == f"Bearer {TOKEN}" for a in Stub.seen_auth))

    def test_info_from_remote_and_local_check(self):
        repo = Repo()
        try:
            repo.write("a.txt", "x\n")
            sha = repo.commit("base")
            repo.git("remote", "add", "origin", "ssh://git@bitbucket.example:7999/shop/order-service.git")
            Stub.routes[PR_PATH.replace("SHOP", "shop")] = Stub.routes[PR_PATH]
            Stub.routes[PR_PATH.replace("SHOP", "shop") + "/merge"] = Stub.routes[PR_PATH + "/merge"]
            code, out, _ = self.run_main(["info", "--pr", "13", "--repo", repo.path])
            self.assertEqual(code, 0)
            local = json.loads(out)["local"]
            self.assertFalse(local["commit_present"])
            self.assertIsNone(local["origin_branch_commit"])
            self.assertTrue(sha)
        finally:
            repo.cleanup()

    def test_text_format(self):
        code, out, _ = self.run_main(["info", self.pr_url(), "--format", "text", "--repo", "/nonexistent"])
        self.assertEqual(code, 0)
        self.assertIn("PR #13", out)
        self.assertIn("sync-phase2", out)

    def test_refused_and_not_found(self):
        Stub.routes[PR_PATH] = (401, {})
        code, _, err = self.run_main(["info", self.pr_url(), "--repo", "/nonexistent"])
        self.assertEqual(code, 3)
        self.assertIn("401", err)
        del Stub.routes[PR_PATH]
        code, _, _ = self.run_main(["info", self.pr_url(), "--repo", "/nonexistent"])
        self.assertEqual(code, 4)

    def test_missing_token(self):
        os.environ.pop("BITBUCKET_TOKEN")
        code, _, err = self.run_main(["info", self.pr_url(), "--repo", "/nonexistent"])
        self.assertEqual(code, 3)
        self.assertIn("BITBUCKET_TOKEN", err)

    def test_basic_auth(self):
        os.environ.pop("BITBUCKET_TOKEN")
        os.environ["BITBUCKET_USER"] = "me"
        os.environ["BITBUCKET_PASSWORD"] = "pw"
        code, _, _ = self.run_main(["info", self.pr_url(), "--repo", "/nonexistent"])
        self.assertEqual(code, 0)
        self.assertTrue(Stub.seen_auth[0].startswith("Basic "))

    def test_find_filters_target(self):
        repo = Repo()
        try:
            repo.git("remote", "add", "origin", f"{self.base}/scm/SHOP/order-service.git")
            code, out, _ = self.run_main(["find", "--source", "sync-phase2", "--target", "staging",
                                          "--repo", repo.path])
            self.assertEqual(code, 0)
            self.assertEqual([p["id"] for p in json.loads(out)["pull_requests"]], [13])
        finally:
            repo.cleanup()

    def test_doctor(self):
        code, out, _ = self.run_main(["doctor", "--repo", "/nonexistent"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual((data["reachable"], data["auth"]), ("yes", "ok"))
        self.assertEqual(data["BITBUCKET_TOKEN"], "set (env)")


class ParseTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("BITBUCKET_URL", None)

    def test_pr_urls(self):
        info = bitbucket_pr.parse_pr_url("https://bb.example/projects/SHOP/repos/svc/pull-requests/13/overview")
        self.assertEqual(info, {"base": "https://bb.example", "project": "SHOP", "repo": "svc", "id": 13})
        info = bitbucket_pr.parse_pr_url("https://bb.example/git/users/dev/repos/svc/pull-requests/2")
        self.assertEqual((info["base"], info["project"]), ("https://bb.example/git", "~dev"))
        self.assertIsNone(bitbucket_pr.parse_pr_url("https://github.com/a/b/pull/1"))

    def test_remotes(self):
        p = bitbucket_pr.parse_remote
        self.assertEqual(p("https://me@bb.example/ctx/scm/shop/svc.git"),
                         {"host": "bb.example", "project": "shop", "repo": "svc", "base": "https://bb.example/ctx"})
        self.assertEqual(p("ssh://git@bb.example:7999/shop/svc.git"),
                         {"host": "bb.example", "project": "shop", "repo": "svc"})
        self.assertEqual(p("git@bb.example:shop/svc.git"), {"host": "bb.example", "project": "shop", "repo": "svc"})

    def test_base_url_refuses_plain_http(self):
        with self.assertRaises(bitbucket_pr.BitbucketError) as ctx:
            bitbucket_pr.base_url("http://bb.example")
        self.assertEqual(ctx.exception.code, 3)
        self.assertEqual(bitbucket_pr.base_url("https://bb.example/"), "https://bb.example")
        os.environ["BITBUCKET_URL"] = "https://configured.example"
        self.assertEqual(bitbucket_pr.base_url("https://from-url.example"), "https://configured.example")

    def test_redact(self):
        text = bitbucket_pr.redact("token: abc123 and https://u:p@host/x and Bearer abcdefghijkl")
        self.assertNotIn("abc123", text)
        self.assertNotIn("u:p@", text)
        self.assertNotIn("abcdefghijkl", text)


if __name__ == "__main__":
    unittest.main()
