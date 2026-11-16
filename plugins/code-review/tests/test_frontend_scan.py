"""Front-end support of pr_scan: file categories, rules, env keys, API calls, npm dependencies, stack."""

import unittest

import helpers  # noqa: F401  (isolated settings, script path)
from helpers import Repo

import pr_scan  # noqa: E402

PACKAGE_V1 = """{
  "name": "cms",
  "dependencies": {
    "next": "14.2.3",
    "react": "^18.2.0",
    "axios": "^1.6.0",
    "lodash": "^4.17.21"
  }
}
"""
PACKAGE_V2 = """{
  "name": "cms",
  "dependencies": {
    "next": "15.0.1",
    "react": "^18.3.1",
    "axios": "^1.6.0",
    "zod": "^3.23.0"
  }
}
"""
LOGIN_V1 = """export default function Login() {
  const go = () => fetch("/api/v1/session/old");
  if (!isAuthenticated()) return null;
  return <a href="/help">help</a>;
}
"""
LOGIN_V2 = """export default function Login({ html }) {
  const go = () => fetch(`/api/v2/session/${id}`);
  localStorage.setItem("accessToken", token);
  const base = process.env.NEXT_PUBLIC_API_SECRET;
  const other = import.meta.env.VITE_BASE_URL;
  console.log(base);
  return <div><div dangerouslySetInnerHTML={{ __html: html }} /><a target="_blank" href="https://partner.example.com/x">x</a></div>;
}
"""


class FrontendScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = cls.repo = Repo()
        r.write("package.json", PACKAGE_V1)
        r.write("src/pages/login.tsx", LOGIN_V1)
        r.write("src/services/orderApi.ts", "export const list = () => http.get('/api/v1/orders');\n")
        r.write("public/locales/vi/common.json", '{"hello": "Xin chào"}\n')
        r.commit("base")
        r.branch("feature/x")
        r.write("package.json", PACKAGE_V2)
        r.write("package-lock.json", '{"lockfileVersion": 3}\n')
        r.write("src/pages/login.tsx", LOGIN_V2)
        r.write("src/services/orderApi.ts", "export const list = () => http.get('/api/v1/orders?page=1');\n")
        r.write("src/pages/__tests__/login.test.tsx", "it('x', () => { eval('1'); });\n")
        r.write("public/locales/vi/common.json", '{"hello": "Xin chào", "bye": "Tạm biệt"}\n')
        r.write("next.config.js", "module.exports = { output: 'standalone' };\n")
        r.commit("FE-1 login page")
        cls.report = pr_scan.scan(r.path, "main", "feature/x", today="20260101", merge=False)

    @classmethod
    def tearDownClass(cls):
        cls.repo.cleanup()

    def cats(self, path):
        return next(f["categories"] for f in self.report["files"] if f["path"] == path)

    def test_stack_and_categories(self):
        self.assertEqual(self.report["range"]["stack"], ["frontend"])
        self.assertEqual(self.cats("src/pages/login.tsx"), ["frontend", "fe-route"])
        self.assertEqual(self.cats("src/services/orderApi.ts"), ["frontend", "fe-api"])
        self.assertEqual(self.cats("src/pages/__tests__/login.test.tsx"), ["test"])
        self.assertEqual(self.cats("public/locales/vi/common.json"), ["i18n"])
        self.assertEqual(self.cats("next.config.js"), ["fe-config"])
        self.assertEqual(self.cats("package.json"), ["build"])

    def test_rules_on_added_lines_only_and_not_in_tests(self):
        kinds = {f["kind"]: f["severity"] for f in self.report["findings"]}
        for kind, sev in (("fe-xss-html", "high"), ("fe-public-secret", "high"), ("fe-token-storage", "medium"),
                          ("fe-hardcoded-url", "medium"), ("fe-blank-target", "medium"), ("fe-console", "info"),
                          ("fe-auth-guard-removed", "medium"), ("fe-api-call-changed", "medium"),
                          ("fe-dependency-major", "medium")):
            self.assertEqual(kinds.get(kind), sev, kind)
        self.assertNotIn("fe-eval", kinds)                 # eval only in a test file

    def test_env_keys_api_calls_and_dependencies(self):
        fe = self.report["frontend"]
        self.assertEqual({e["key"] for e in fe["env_keys"]}, {"NEXT_PUBLIC_API_SECRET", "VITE_BASE_URL"})
        calls = {(a["change"], a["path"]) for a in fe["api_calls"]}
        self.assertIn(("bỏ", "/api/v1/session/old"), calls)
        self.assertIn(("thêm", "/api/v2/session/${id}"), calls)
        self.assertEqual(fe["routes"], ["src/pages/login.tsx"])
        deps = {d["artifact"]: (d["change"], d.get("old"), d.get("new")) for d in self.report["dependencies"]}
        self.assertEqual(deps["next"], ("sửa", "14.2.3", "15.0.1"))
        self.assertEqual(deps["zod"][0], "thêm")
        self.assertEqual(deps["lodash"][0], "bỏ")
        self.assertNotIn("axios", deps)

    def test_review_files_are_sources_largest_first_without_tests(self):
        paths = [f["path"] for f in self.report["review_files"]]
        self.assertEqual(paths[0], "src/pages/login.tsx")
        self.assertIn("next.config.js", paths)
        self.assertNotIn("src/pages/__tests__/login.test.tsx", paths)
        self.assertNotIn("package.json", paths)

    def test_text_output_has_a_front_end_block(self):
        text = pr_scan.render_text(self.report)
        self.assertIn("Stack: frontend", text)
        self.assertIn("env read: NEXT_PUBLIC_API_SECRET", text)


class StackTest(unittest.TestCase):
    def test_spring_and_frontend_in_one_repository(self):
        r = Repo()
        try:
            r.write("pom.xml", "<project/>\n")
            r.write("web/package.json", '{"dependencies": {"vue": "^3.4.0"}}\n')
            sha = r.commit("base")
            self.assertEqual(pr_scan.detect_stack(r.path, sha), ["spring", "frontend"])
        finally:
            r.cleanup()


if __name__ == "__main__":
    unittest.main()
