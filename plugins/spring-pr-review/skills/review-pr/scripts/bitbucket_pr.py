#!/usr/bin/env python3
"""Read one Bitbucket Server / Data Center pull request: title, branches, commits, merge status, builds.

GET requests only: nothing is created, approved, commented or merged. The token is never printed.

Settings (~/.dev-cli/.env, or a process environment variable with the same name):
  BITBUCKET_URL         base URL, e.g. https://bitbucket.example.com (optional when a PR URL is given)
  BITBUCKET_TOKEN       HTTP access token, sent as Bearer. Or, instead:
  BITBUCKET_USER, BITBUCKET_PASSWORD   basic authentication
  BITBUCKET_CA_BUNDLE   CA file for an internal certificate authority. TLS verification is never turned off.

Usage:
  bitbucket_pr.py doctor [--repo DIR]
  bitbucket_pr.py info <PR URL> [--repo DIR]
  bitbucket_pr.py info --pr 13 [--repo DIR]           project and repository from the git remote "origin"
  bitbucket_pr.py find --source feature/x [--target staging] [--repo DIR]

Exit codes: 0 ok, 2 usage, 3 not configured or refused (401/403), 4 not found, 5 network or TLS error.
"""

import argparse
import base64
import datetime
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugin_settings import setting  # noqa: E402

TIMEOUT = 20
PR_URL_RE = re.compile(
    r"^(?P<base>https?://[^/]+(?:/[^/]+)*?)/(?:projects/(?P<project>[^/]+)|users/(?P<user>[^/]+))"
    r"/repos/(?P<repo>[^/]+)/pull-requests/(?P<id>\d+)"
)
REMOTE_RES = (
    re.compile(r"^(?P<scheme>https?)://(?:[^@/]+@)?(?P<host>[^/]+)(?P<ctx>/.*?)?/scm/(?P<project>[^/]+)/"
               r"(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^ssh://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<project>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<project>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
)
SCRUB_RES = (
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[-_]?key)\b(\s*[:=]\s*)\S+"), r"\1\2<redacted>"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 <redacted>"),
    (re.compile(r"://[^/\s:@]+:[^/\s@]+@"), "://<redacted>@"),
)


class BitbucketError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def redact(text):
    for rx, repl in SCRUB_RES:
        text = rx.sub(repl, text)
    return text


def parse_pr_url(url):
    m = PR_URL_RE.match(url.strip())
    if not m:
        return None
    project = m.group("project") or "~" + m.group("user")
    return {"base": m.group("base"), "project": project, "repo": m.group("repo"), "id": int(m.group("id"))}


def parse_remote(url):
    url = url.strip()
    for rx in REMOTE_RES:
        m = rx.match(url)
        if m:
            info = {"host": m.group("host"), "project": m.group("project"), "repo": m.group("repo")}
            if "scheme" in m.groupdict() and m.group("scheme"):
                info["base"] = f"{m.group('scheme')}://{m.group('host')}{m.group('ctx') or ''}"
            return info
    return None


def git_remote(repo):
    proc = subprocess.run(["git", "-C", repo, "remote", "get-url", "origin"], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def base_url(explicit=None):
    configured, _ = setting("BITBUCKET_URL")
    base = (configured or explicit or "").rstrip("/")
    if not base:
        raise BitbucketError("chưa có BITBUCKET_URL (thêm vào ~/.dev-cli/.env hoặc đưa link PR đầy đủ)", 3)
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise BitbucketError("BITBUCKET_URL phải là https:// (không gửi token qua http)", 3)
    if parsed.scheme not in ("http", "https"):
        raise BitbucketError(f"BITBUCKET_URL không hợp lệ: {base}", 3)
    return base


def auth_header():
    token, _ = setting("BITBUCKET_TOKEN")
    if token:
        return "Bearer " + token
    user, _ = setting("BITBUCKET_USER")
    password, _ = setting("BITBUCKET_PASSWORD")
    if user and password:
        return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    raise BitbucketError("chưa cấu hình BITBUCKET_TOKEN (hoặc BITBUCKET_USER + BITBUCKET_PASSWORD) "
                         "trong ~/.dev-cli/.env", 3)


def ssl_context():
    ca, _ = setting("BITBUCKET_CA_BUNDLE")
    if ca:
        path = os.path.expanduser(ca)
        if not os.path.isfile(path):
            raise BitbucketError(f"BITBUCKET_CA_BUNDLE không tồn tại: {path}", 3)
        return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


class Client:
    def __init__(self, base, auth=True):
        self.base = base
        self.headers = {"Accept": "application/json"}
        if auth:
            self.headers["Authorization"] = auth_header()
        self.context = ssl_context() if base.startswith("https://") else None

    def get(self, path, params=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        request = urllib.request.Request(url, headers=self.headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=self.context) as resp:
                return json.loads(resp.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise BitbucketError(f"Bitbucket từ chối ({exc.code}) {path}: token sai, hết hạn hoặc thiếu quyền đọc", 3)
            if exc.code == 404:
                raise BitbucketError(f"không tìm thấy (404) {path}", 4)
            raise BitbucketError(f"Bitbucket trả lỗi {exc.code} cho {path}", 5)
        except ssl.SSLError as exc:
            raise BitbucketError(f"lỗi TLS: {exc.reason or exc}; nếu Bitbucket dùng CA nội bộ, khai "
                                 "BITBUCKET_CA_BUNDLE (không tắt kiểm tra chứng chỉ)", 5)
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                raise BitbucketError(f"lỗi TLS: {reason.reason or reason}; nếu Bitbucket dùng CA nội bộ, khai "
                                     "BITBUCKET_CA_BUNDLE (không tắt kiểm tra chứng chỉ)", 5)
            raise BitbucketError(f"không kết nối được Bitbucket: {reason}", 5)
        except (TimeoutError, OSError) as exc:
            raise BitbucketError(f"không kết nối được Bitbucket: {exc}", 5)
        except ValueError:
            raise BitbucketError(f"Bitbucket trả về dữ liệu không phải JSON cho {path}", 5)


def date_of(ms):
    if not ms:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


def repo_path(project, repo):
    return f"/rest/api/1.0/projects/{urllib.parse.quote(project)}/repos/{urllib.parse.quote(repo)}"


def locate(args):
    """-> (base, project, repo, pr id or None) from a PR URL or from the git remote."""
    if getattr(args, "url", None):
        info = parse_pr_url(args.url)
        if not info:
            raise BitbucketError("link PR không đúng dạng .../projects/<P>/repos/<R>/pull-requests/<số>", 2)
        return base_url(info["base"]), info["project"], info["repo"], info["id"]
    remote = git_remote(args.repo)
    info = parse_remote(remote) if remote else None
    if not info:
        raise BitbucketError("không đọc được project/repo từ git remote origin; đưa link PR đầy đủ", 2)
    return base_url(info.get("base")), info["project"], info["repo"], getattr(args, "pr", None)


def local_check(repo, branch, commit):
    def git(*a):
        p = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
        return p.returncode, p.stdout.strip()

    present = git("cat-file", "-e", f"{commit}^{{commit}}")[0] == 0 if commit else False
    rc, origin = git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}")
    return {"commit_present": present, "origin_branch": f"origin/{branch}",
            "origin_branch_commit": origin if rc == 0 else None,
            "matches": bool(commit and rc == 0 and origin == commit)}


def build_summary(values):
    counts = {}
    for v in values:
        counts[v.get("state", "?")] = counts.get(v.get("state", "?"), 0) + 1
    return ", ".join(f"{k} {n}" for k, n in sorted(counts.items())) or "không có build status nào"


def cmd_info(args):
    base, project, repo, pr_id = locate(args)
    if not pr_id:
        raise BitbucketError("thiếu số PR (--pr) hoặc link PR", 2)
    client = Client(base)
    rp = repo_path(project, repo)
    pr = client.get(f"{rp}/pull-requests/{pr_id}")
    from_ref, to_ref = pr.get("fromRef", {}), pr.get("toRef", {})
    head = from_ref.get("latestCommit")

    try:
        m = client.get(f"{rp}/pull-requests/{pr_id}/merge")
        merge = {"can_merge": m.get("canMerge"), "conflicted": m.get("conflicted"), "outcome": m.get("outcome"),
                 "vetoes": [redact(v.get("summaryMessage", "")) for v in m.get("vetoes", [])]}
    except BitbucketError as exc:
        merge = {"error": str(exc)}

    try:
        b = client.get(f"/rest/build-status/1.0/commits/{head}") if head else {"values": []}
        values = [{"state": v.get("state"), "name": v.get("name") or v.get("key"), "url": v.get("url")}
                  for v in b.get("values", [])]
        builds = {"summary": build_summary(values), "values": values}
    except BitbucketError as exc:
        builds = {"error": str(exc)}

    try:
        c = client.get(f"{rp}/pull-requests/{pr_id}/commits", {"limit": 100})
        commit_count = f"{c.get('size', 0)}{'' if c.get('isLastPage', True) else '+'}"
    except BitbucketError:
        commit_count = None

    links = pr.get("links", {}).get("self", [])
    result = {
        "pr": {
            "id": pr.get("id"),
            "title": redact(pr.get("title", "")),
            "description": redact(pr.get("description", "") or "")[:1500],
            "state": pr.get("state"),
            "author": pr.get("author", {}).get("user", {}).get("displayName"),
            "created": date_of(pr.get("createdDate")),
            "updated": date_of(pr.get("updatedDate")),
            "url": links[0].get("href") if links else None,
            "project": project,
            "repo": repo,
            "from": {"branch": from_ref.get("displayId"), "commit": head},
            "to": {"branch": to_ref.get("displayId"), "commit": to_ref.get("latestCommit")},
            "reviewers": [{"name": r.get("user", {}).get("displayName"), "status": r.get("status")}
                          for r in pr.get("reviewers", [])],
            "commit_count": commit_count,
        },
        "merge": merge,
        "builds": builds,
    }
    if args.repo and os.path.isdir(args.repo):
        result["local"] = local_check(args.repo, from_ref.get("displayId"), head)
    return result


def cmd_find(args):
    base, project, repo, _ = locate(args)
    client = Client(base)
    params = {"state": "OPEN", "direction": "OUTGOING", "at": f"refs/heads/{args.source}", "limit": 25}
    data = client.get(f"{repo_path(project, repo)}/pull-requests", params)
    out = []
    for pr in data.get("values", []):
        to_branch = pr.get("toRef", {}).get("displayId")
        if args.target and to_branch != args.target:
            continue
        links = pr.get("links", {}).get("self", [])
        out.append({"id": pr.get("id"), "title": redact(pr.get("title", "")),
                    "from": pr.get("fromRef", {}).get("displayId"), "to": to_branch,
                    "url": links[0].get("href") if links else None})
    return {"project": project, "repo": repo, "pull_requests": out}


def cmd_doctor(args):
    report = {}
    for key in ("BITBUCKET_URL", "BITBUCKET_TOKEN", "BITBUCKET_USER", "BITBUCKET_PASSWORD", "BITBUCKET_CA_BUNDLE"):
        value, source = setting(key)
        report[key] = f"set ({source})" if value else "not set"
    remote = git_remote(args.repo) if args.repo else ""
    info = parse_remote(remote) if remote else None
    report["remote"] = f"{info['project']}/{info['repo']}" if info else "không đọc được"
    try:
        base = base_url(info.get("base") if info else None)
        report["base_url"] = base
        Client(base, auth=False).get("/rest/api/1.0/application-properties")
        report["reachable"] = "yes"
        Client(base).get("/rest/api/1.0/projects", {"limit": 1})
        report["auth"] = "ok"
    except BitbucketError as exc:
        report["error"] = str(exc)
        report["exit"] = exc.code
    return report


def render_text(result):
    if "pr" not in result:
        return json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    pr, merge, builds = result["pr"], result["merge"], result["builds"]
    lines = [
        f"PR #{pr['id']} {pr['title']}  [{pr['state']}]",
        f"  {pr['from']['branch']} ({(pr['from']['commit'] or '')[:8]}) -> {pr['to']['branch']} "
        f"({(pr['to']['commit'] or '')[:8]})",
        f"  author: {pr['author']}  created {pr['created']}  updated {pr['updated']}  commits: {pr['commit_count']}",
        "  merge: " + (merge.get("error") or f"can_merge={merge.get('can_merge')} conflicted={merge.get('conflicted')}"
                       + (f" vetoes: {'; '.join(merge['vetoes'])}" if merge.get("vetoes") else "")),
        "  builds: " + (builds.get("error") or builds["summary"]),
        "  reviewers: " + (", ".join(f"{r['name']} ({r['status']})" for r in pr["reviewers"]) or "-"),
    ]
    if "local" in result:
        loc = result["local"]
        lines.append(f"  local: commit present={loc['commit_present']}  {loc['origin_branch']}="
                     f"{(loc['origin_branch_commit'] or '-')[:8]}  matches={loc['matches']}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read a Bitbucket Server pull request (GET only).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("info", help="one pull request: branches, commits, merge status, builds")
    p.add_argument("url", nargs="?", help="pull-request URL")
    p.add_argument("--pr", type=int, help="pull-request number (project/repo from git remote origin)")
    p.add_argument("--repo", default=".", help="local repository (default: current folder)")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p = sub.add_parser("find", help="open pull requests from a source branch")
    p.add_argument("--source", required=True)
    p.add_argument("--target")
    p.add_argument("--url", help=argparse.SUPPRESS)
    p.add_argument("--repo", default=".")
    p.add_argument("--format", choices=["json"], default="json")
    p = sub.add_parser("doctor", help="settings, reachability and authentication")
    p.add_argument("--repo", default=".")
    p.add_argument("--format", choices=["json"], default="json")
    args = parser.parse_args(argv)

    if args.command == "info" and not args.url and not args.pr:
        parser.error("info needs a pull-request URL or --pr N")
    try:
        if args.command == "info":
            result = cmd_info(args)
        elif args.command == "find":
            result = cmd_find(args)
        else:
            result = cmd_doctor(args)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return result.get("exit", 0)
    except BitbucketError as exc:
        print(f"bitbucket_pr: {exc}", file=sys.stderr)
        return exc.code

    if args.format == "text":
        sys.stdout.write(render_text(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
