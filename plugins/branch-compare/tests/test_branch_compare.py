import contextlib
import io
import json
import os
import tempfile
import unittest

import helpers  # noqa: F401  (isolated settings, script path)
from helpers import Repo

import branch_compare  # noqa: E402

J = "src/main/java/com/shop/order"
RES = "src/main/resources"
PROPS = f"{RES}/application.properties"
FLYWAY = f"{RES}/db/migration"


class CompareCase(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.repo.write(PROPS, "app.name=order\n")
        self.repo.write(f"{J}/OrderService.java", "class OrderService {\n  int a = 1;\n}\n")
        self.repo.write("README.md", "# order\n")
        self.repo.commit("base")
        self.repo.branch("staging")

    def tearDown(self):
        self.repo.cleanup()

    def compare(self, **kw):
        return branch_compare.compare(self.repo.path, "staging", "release", today="20260101", **kw)

    def head(self):
        return self.repo.git("rev-parse", "--short", "HEAD").strip()

    @staticmethod
    def kinds(report, severity=None):
        return {f["kind"] for f in report["findings"] if severity is None or f["severity"] == severity}


class ClassificationTest(CompareCase):
    def test_missing_already_maybe_reverted_no_effect_and_target_only(self):
        r = self.repo
        r.write(f"{J}/PriceCalculator.java", "class PriceCalculator {}\n")
        same = r.commit("PROJ-2 fix price rounding")
        r.write(f"{J}/ArchiveJob.java", "class ArchiveJob {}\n")
        picked = r.commit("PROJ-1 archive job")
        r.write(f"{J}/ArchiveConsumer.java", "class ArchiveConsumer {}\n")
        r.commit("PROJ-1 archive consumer")
        missing = self.head()
        r.write(f"{J}/Log.java", "class Log {}\n")
        tweak = r.commit("tweak logging")
        r.git("revert", "--no-edit", tweak)
        r.write("README.md", "# order\nrun with java 21\n")
        r.commit("docs: java version")
        docs_1 = self.head()
        r.write("README.md", "# order\nrun with java 21\nport 8080\n")
        r.commit("docs: port")
        docs_2 = self.head()
        r.write(f"{J}/Guard.java", "class Guard {}\n")
        r.commit("fix null check")
        maybe = self.head()

        r.checkout("release")
        r.write(f"{J}/Banner.java", "class Banner {}\n")
        r.commit("release banner")          # the target moved on before the cherry-picks
        r.git("cherry-pick", same)
        same_on_target = self.head()
        r.write(f"{J}/ArchiveJob.java", "class ArchiveJob {}\n")
        r.write(f"{J}/ArchiveJobConfig.java", "class ArchiveJobConfig {}\n")
        r.commit(f"PROJ-1 archive job\n\n(cherry picked from commit {picked})")
        picked_on_target = self.head()
        r.write("README.md", "# order\nrun with java 21\nport 8080\n")
        r.commit("docs squashed")           # same content as the two source commits, one patch
        docs_on_target = self.head()
        r.write(f"{J}/Validator.java", "class Validator {}\n")
        r.commit("fix null check")
        r.write(f"{J}/OrderService.java", "class OrderService {\n  int a = 1;\n  int b = 2;\n}\n")
        r.commit("PROJ-9 hotfix guard")
        hotfix = self.head()
        r.write(f"{RES}/db/changelog/changes/hotfix/001-fix.sql", "update t set a = 1 where id = 2;\n")
        r.commit("hotfix data")

        report = self.compare()
        c = report["commits"]
        self.assertEqual([d["short"] for d in c["missing"]], [missing])
        self.assertEqual({(d["subject"], d["how"], d["target"]) for d in c["already_on_target"]},
                         {("PROJ-2 fix price rounding", "cùng nội dung", same_on_target),
                          ("PROJ-1 archive job", "cherry-pick -x", picked_on_target)})
        self.assertEqual([(d["short"], d["target"]["subject"]) for d in c["maybe_on_target"]],
                         [(maybe, "fix null check")])
        self.assertEqual([p["commit"]["subject"] for p in c["reverted"]], ["tweak logging"])
        self.assertEqual([d["short"] for d in c["no_net_effect"]], [docs_1, docs_2])
        only = {d["short"]: d for d in report["target_only"]}
        self.assertTrue(only[hotfix]["back_merge_effect"])
        self.assertFalse(only[docs_on_target]["back_merge_effect"])
        self.assertNotIn(same_on_target, only)
        self.assertNotIn(picked_on_target, only)
        self.assertEqual(report["partial_tickets"],
                         [{"ticket": "PROJ-1", "missing": [missing], "on_target": [picked_on_target]}])
        self.assertEqual(report["target_only_migrations"], [f"{RES}/db/changelog/changes/hotfix/001-fix.sql"])
        counts = report["range"]["counts"]
        self.assertEqual((counts["missing"], counts["already_on_target"], counts["target_only"]), (1, 2, 4))
        self.assertEqual(report["range"]["diff_basis"], "merge-result")
        self.assertEqual(report["range"]["merge"]["status"], "clean")

    def test_the_same_patch_twice_in_the_source_is_listed_once(self):
        r = self.repo
        r.write(f"{J}/OrderService.java", "class OrderService {\n  int a = 1;\n  int login = 1;\n}\n")
        first = r.commit("fix login filter")
        first_short = self.head()
        r.write(f"{J}/OrderService.java", "class OrderService {\n  int a = 1;\n}\n")
        r.commit("cleanup")
        r.git("cherry-pick", first)
        copy = self.head()
        report = self.compare()
        missing = report["commits"]["missing"]
        self.assertEqual([d["subject"] for d in missing], ["fix login filter", "cleanup"])
        self.assertEqual(missing[0]["copies"], [copy])
        self.assertEqual([(d["short"], d["duplicate_of"]) for d in report["commits"]["duplicates"]],
                         [(copy, first_short)])
        self.assertEqual(report["range"]["counts"]["duplicates"], 1)

    def test_conflict_lists_the_commits_of_both_sides(self):
        r = self.repo
        r.write(f"{J}/OrderService.java", "class OrderService {\n  int a = 2;\n}\n")
        r.commit("change a")
        source = self.head()
        r.checkout("release")
        r.write(f"{J}/OrderService.java", "class OrderService {\n  int a = 3;\n}\n")
        r.commit("hotfix a")
        target = self.head()
        report = self.compare()
        self.assertEqual(report["range"]["merge"]["status"], "conflict")
        self.assertEqual(report["conflicts"],
                         [{"file": f"{J}/OrderService.java", "source_commits": [source], "target_commits": [target]}])
        self.assertIn("merge-conflict", report["commits"]["missing"][0]["flags"])
        self.assertIn("merge-conflict", report["target_only"][0]["flags"])

    def test_cherry_picked_migration_is_not_part_of_the_net_change(self):
        r = self.repo
        r.write(f"{FLYWAY}/V1__init.sql", "create table t (id int);\n")
        r.commit("V1")
        r.checkout("release")
        r.git("merge", "-q", "--ff-only", "staging")
        r.checkout("staging")
        r.write(f"{FLYWAY}/V2__add_a.sql", "alter table t add column a int;\n")
        v2 = r.commit("V2 add a")
        r.write(f"{FLYWAY}/V3__drop_b.sql", "alter table t drop column b;\n")
        r.commit("V3 drop b")
        r.checkout("release")
        r.write("NOTES.md", "release notes\n")
        r.commit("release notes")
        r.git("cherry-pick", v2)
        report = self.compare()
        self.assertEqual([m["file"] for m in report["migrations"]], [f"{FLYWAY}/V3__drop_b.sql"])
        self.assertIn("sql-drop", self.kinds(report, "high"))
        self.assertNotIn("migration-existing-changed", self.kinds(report))
        self.assertEqual(report["range"]["counts"]["already_on_target"], 1)

    def test_no_merge_check_falls_back_to_the_merge_base(self):
        self.repo.write(f"{J}/A.java", "class A {}\n")
        self.repo.commit("add A")
        report = self.compare(merge=False)
        self.assertEqual(report["range"]["diff_basis"], "merge-base")
        self.assertEqual(report["range"]["merge"]["status"], "skipped")
        self.assertEqual(report["range"]["counts"]["missing"], 1)


class ReleaseChecksTest(CompareCase):
    def test_required_keys_without_default(self):
        r = self.repo
        r.write(f"{RES}/application.yml", "app:\n  archive:\n    batch-size: 10\n")
        r.commit("yaml")
        r.checkout("release")
        r.git("merge", "-q", "--ff-only", "staging")
        r.checkout("staging")
        r.write(f"{J}/ArchiveJob.java",
                'class ArchiveJob {\n  @Value("${app.archive.cron}") String cron;\n'
                '  @Value("${app.archive.batch-size}") int size;\n'
                '  @Value("${app.archive.enabled:false}") boolean on;\n}\n')
        r.append(PROPS, "spring.datasource.url=${DB_URL}\nfeature.x=${FEATURE_X:off}\nnested=${A:${B}}\n")
        r.commit("archive job")
        report = self.compare()
        flagged = sorted(f["detail"].split("`")[1] for f in report["findings"] if f["kind"] == "config-required-key")
        self.assertEqual(flagged, ["DB_URL", "app.archive.cron"])
        self.assertEqual(report["range"]["env_check_keys"], "app.archive.cron,DB_URL")
        self.assertIn("config-required-key", report["commits"]["missing"][0]["flags"])
        covered = [k for k in report["required_keys"] if k["key"] == "app.archive.batch-size"][0]
        self.assertEqual(covered["packaged_in"], f"{RES}/application.yml")

    def test_environment_files_and_staging_values(self):
        r = self.repo
        r.write(f"{RES}/application-uat.properties", "x=1\n")
        r.write(f"{RES}/application-staging.properties", "x=2\n")
        r.write("helm/values-prod.yaml", "replicas: 2\n")
        r.append(PROPS, "payment.url=http://localhost:8080\nsafe.url=${PAY_URL:http://localhost}\n"
                        "partner.host=partner.example\n")
        r.commit("env config")
        report = self.compare()
        target_files = sorted(f["file"] for f in report["findings"] if f["kind"] == "config-target-env-file")
        self.assertEqual(target_files, ["helm/values-prod.yaml", f"{RES}/application-uat.properties"])
        self.assertIn("config-source-env-file", self.kinds(report, "info"))
        staging = [f for f in report["findings"] if f["kind"] == "config-staging-value"]
        self.assertEqual([f["detail"].split("`")[1] for f in staging], ["payment.url"])
        self.assertNotIn("localhost:8080", json.dumps(report))
        self.assertNotIn("config-required-key", self.kinds(report))

    def test_message_classes_schema_and_build_versions(self):
        r = self.repo
        r.write(f"{J}/event/OrderEvent.java",
                "class OrderEvent {\n  private String id;\n  private long amount;\n}\n")
        r.write(f"{J}/event/OrderEventPublisher.java", "class OrderEventPublisher {\n  private String topic;\n}\n")
        r.write("pom.xml", "<project>\n<properties>\n<promix.version>2.0.0</promix.version>\n"
                           "<java.version>17</java.version>\n</properties>\n</project>\n")
        r.commit("event")
        r.checkout("release")
        r.git("merge", "-q", "--ff-only", "staging")
        r.checkout("staging")
        r.write(f"{J}/event/OrderEvent.java",
                "class OrderEvent {\n  private String id;\n  private String currency;\n}\n")
        r.write(f"{J}/event/OrderEventPublisher.java", "class OrderEventPublisher {\n  private String name;\n}\n")
        r.write("pom.xml", "<project>\n<properties>\n<promix.version>2.0.1-SNAPSHOT</promix.version>\n"
                           "<java.version>21</java.version>\n</properties>\n</project>\n")
        r.write("src/main/avro/order.avsc", '{"type": "record", "name": "Order"}\n')
        r.commit("PROJ-3 change event")
        report = self.compare()
        removed = [f for f in report["findings"] if f["kind"] == "message-field-removed"]
        self.assertEqual([f["file"] for f in removed], [f"{J}/event/OrderEvent.java"])
        self.assertIn("amount", removed[0]["detail"])
        self.assertIn("message-schema-changed", self.kinds(report, "medium"))
        self.assertIn("dependency-snapshot", self.kinds(report, "medium"))
        versions = {f["detail"].split("`")[1]: f["severity"] for f in report["findings"] if f["kind"] == "version-changed"}
        self.assertEqual(versions, {"java.version": "medium", "promix.version": "info"})

    def test_sensitive_areas(self):
        r = self.repo
        r.write(f"{J}/PaymentService.java", "class PaymentService {}\n")
        r.write(f"{J}/FeedbackService.java", "class FeedbackService {}\n")
        r.write(f"{J}/CleanupTask.java",
                "class CleanupTask {\n  @Scheduled(cron = \"0 0 * * * *\")\n  void run() { repo.deleteByCreatedBefore(x); }\n}\n")
        r.commit("cleanup")
        areas = self.compare()["commits"]["missing"][0]["areas"]
        self.assertEqual(areas, ["job định kỳ", "tiền/giá", "xóa dữ liệu"])

    def test_kafka_consumer_adds_the_offset_key_to_check(self):
        self.repo.write(f"{J}/OrderConsumer.java",
                        'class OrderConsumer {\n  @KafkaListener(topics = "${app.topic}", groupId = "order-group")\n'
                        '  void on(String m) {}\n}\n')
        self.repo.commit("consumer")
        report = self.compare()
        self.assertIn("spring.kafka.consumer.auto-offset-reset", report["range"]["env_check_keys"])


class CliTest(CompareCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = branch_compare.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_bare_name_prefers_origin_and_files_are_written(self):
        r = self.repo
        r.write(f"{J}/A.java", "class A {}\n")
        first = r.commit("PROJ-5 one")
        r.write(f"{J}/B.java", "class B {}\n")
        r.commit("PROJ-5 two")
        r.git("update-ref", "refs/remotes/origin/staging", first)
        report = self.compare()
        self.assertEqual(report["range"]["source_ref"], "origin/staging")
        self.assertEqual(report["range"]["target_ref"], "release")
        self.assertEqual(report["range"]["counts"]["missing"], 1)
        self.assertEqual(report["range"]["report_name"], "compare-staging-to-release-order-service-20260101.md")

        tmp = tempfile.mkdtemp(prefix="bc-out-")
        out_file, patch_file = os.path.join(tmp, "a", "compare.json"), os.path.join(tmp, "b", "merge.patch")
        code, out, _ = self.run_main(["--repo", r.path, "--format", "text", "--out", out_file,
                                      "--patch-file", patch_file])
        self.assertEqual(code, 0)
        self.assertIn("Missing commits:", out)
        with open(out_file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["range"]["counts"]["missing"], 1)
        with open(patch_file, encoding="utf-8") as fh:
            self.assertIn("diff --git a/src/main/java/com/shop/order/A.java", fh.read())

    def test_unknown_branch_and_not_a_repository(self):
        code, _, err = self.run_main(["--repo", self.repo.path, "--source", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("git fetch origin nope", err)
        code, _, err = self.run_main(["--repo", tempfile.mkdtemp(prefix="bc-empty-")])
        self.assertEqual(code, 2)
        self.assertIn("không phải git repository", err)


class UnitTest(unittest.TestCase):
    def test_env_side(self):
        side = branch_compare.env_side
        envs = branch_compare.DEFAULT_TARGET_ENVS
        self.assertEqual(side(f"{RES}/application-uat.yml", envs), ("target", "uat"))
        self.assertEqual(side(f"{RES}/application-prod-k8s.yml", envs), ("target", "prod"))
        self.assertEqual(side("deploy/production/values.yaml", envs), ("target", "production"))
        self.assertEqual(side(".env.staging", envs), ("source", "staging"))
        self.assertEqual(side(f"{RES}/application.yml", envs), (None, None))
        self.assertEqual(side("src/test/resources/application-test.yml", envs), (None, None))

    def test_tickets(self):
        self.assertEqual(branch_compare.tickets_of("PROJ-12 fix UTF-8 for SHOP-7, PROJ-12 again"), ["PROJ-12", "SHOP-7"])

    def test_flatten_yaml(self):
        text = "spring:\n  kafka:\n    consumer:\n      group-id: g\n---\napp:\n  - name: x\n"
        keys = branch_compare.flatten_yaml(text)
        self.assertIn("spring.kafka.consumer.group-id", keys)
        self.assertIn("app.name", keys)


if __name__ == "__main__":
    unittest.main()
