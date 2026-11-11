import contextlib
import io
import json
import os
import tempfile
import unittest

import helpers  # noqa: F401  (isolated settings, script path)

import env_overrides  # noqa: E402

FAKE_APP_CONFIG = r'''
import json, sys
args = sys.argv[1:]
env = args[args.index("--env") + 1]
if env == "production":
    print("error: no production folder for svc", file=sys.stderr)
    sys.exit(4)
files = {
    "staging": {"application.properties": {"app.kafka.topics.order-commands": "import-batch-events",
                                           "spring.kafka.consumer.group-id": "svc-group",
                                           "spring.datasource.password": "<secret>"}},
    "uat": {"app.env": {"APP_KAFKA_TOPICS_ORDERCOMMANDS": "uat|topic"}},
}[env]
print(json.dumps({"service": "svc", "environment": env, "repository": "R", "commit": "abc1234", "files": files}))
'''


class EnvOverridesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spr-env-")
        self.fake = os.path.join(self.tmp, "app_config.py")
        with open(self.fake, "w", encoding="utf-8") as fh:
            fh.write(FAKE_APP_CONFIG)
        os.environ["SPRING_PR_REVIEW_APP_CONFIG"] = self.fake

    def tearDown(self):
        os.environ.pop("SPRING_PR_REVIEW_APP_CONFIG", None)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = env_overrides.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_relaxed_binding_leaf_names_and_errors(self):
        code, out, _ = self.run_main(["--service", "svc", "--envs", "staging,uat,production",
                                      "--keys", "app.kafka.topics.order-commands,group-id,new.key"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        rows = {r["key"]: r["envs"] for r in data["keys"]}
        topic = rows["app.kafka.topics.order-commands"]
        self.assertEqual(topic["staging"]["status"], "ghi đè")
        self.assertEqual(topic["uat"]["values"][0]["key"], "APP_KAFKA_TOPICS_ORDERCOMMANDS")
        self.assertEqual(topic["production"]["status"], "lỗi đọc")
        self.assertEqual(rows["group-id"]["staging"]["values"][0]["key"], "spring.kafka.consumer.group-id")
        self.assertEqual(rows["new.key"]["staging"]["status"], "—")
        self.assertIn("production", data["errors"])

    def test_markdown_escapes_pipes_and_keeps_masks(self):
        code, out, _ = self.run_main(["--service", "svc", "--envs", "staging,uat", "--format", "md",
                                      "--keys", "app.kafka.topics.order-commands,"
                                                "spring.datasource.password"])
        self.assertEqual(code, 0)
        self.assertIn("| Key | staging | uat |", out)
        self.assertIn("uat\\|topic", out)
        self.assertIn("`<secret>`", out)

    def test_keys_from_scan_take_packaged_config_only(self):
        scan = os.path.join(self.tmp, "scan.json")
        with open(scan, "w", encoding="utf-8") as fh:
            json.dump({"config_changes": [
                {"file": "src/main/resources/application.properties", "key": "a.b", "change": "thêm"},
                {"file": "helm/values-staging.yaml", "key": "replicas", "change": "sửa"},
                {"file": "src/main/resources/application-uat.yml", "key": "c.d", "change": "bỏ"},
            ]}, fh)
        self.assertEqual(env_overrides.keys_from_scan(scan), ["a.b", "c.d"])

    def test_no_keys_and_missing_app_config(self):
        self.assertEqual(self.run_main(["--service", "svc"])[0], 2)
        os.environ["SPRING_PR_REVIEW_APP_CONFIG"] = os.path.join(self.tmp, "missing.py")
        original = env_overrides.find_app_config
        env_overrides.find_app_config = lambda: None
        try:
            code, _, err = self.run_main(["--service", "svc", "--keys", "a"])
        finally:
            env_overrides.find_app_config = original
        self.assertEqual(code, 3)
        self.assertIn("SPRING_PR_REVIEW_APP_CONFIG", err)

    def test_canonical(self):
        c = env_overrides.canonical
        self.assertEqual(c("app.kafka.topics.order-commands"),
                         c("APP_KAFKA_TOPICS_ORDERCOMMANDS"))


if __name__ == "__main__":
    unittest.main()
