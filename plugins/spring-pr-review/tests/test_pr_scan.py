import contextlib
import io
import json
import unittest

import helpers
from helpers import Repo

import pr_scan  # noqa: E402  (path set by helpers)

CHANGES = "src/main/resources/db/changelog/changes"
MASTER = "src/main/resources/db/changelog/db.changelog-master.yaml"
FLYWAY = "src/main/resources/db/migration"
PROPS = "src/main/resources/application.properties"


def include(name):
    return f"  - include:\n      file: changes/{name}\n"


class ScanCase(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def scan(self, base="main", head="feature/x", **kw):
        return pr_scan.scan(self.repo.path, base, head, today="20260101", **kw)

    def kinds(self, report, severity=None):
        return {f["kind"] for f in report["findings"] if severity is None or f["severity"] == severity}

    def liquibase_base(self, *names):
        self.repo.write(MASTER, "databaseChangeLog:\n" + "".join(include(n) for n in names))
        for n in names:
            self.repo.write(f"{CHANGES}/{n}", "databaseChangeLog:\n  - changeSet:\n      id: x\n      changes:\n"
                                              "        - createTable:\n            tableName: t\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")


class ChangelogIndexTest(ScanCase):
    def test_appending_an_include_to_the_master_is_not_a_changed_migration(self):
        self.liquibase_base("001-init.yaml")
        self.repo.append(MASTER, include("002-next.yaml"))
        self.repo.write(f"{CHANGES}/002-next.yaml", "databaseChangeLog:\n  - changeSet:\n      id: 2\n")
        self.repo.commit("add 002")
        report = self.scan()
        self.assertNotIn("migration-existing-changed", self.kinds(report))
        self.assertNotIn("lb-not-included", self.kinds(report))
        master = [m for m in report["migrations"] if m["file"] == MASTER][0]
        self.assertTrue(master["changelog_index"])

    def test_changing_an_existing_include_path_is_high(self):
        self.liquibase_base("001-init.yaml")
        self.repo.write(MASTER, "databaseChangeLog:\n" + include("001-renamed.yaml"))
        self.repo.commit("rename include")
        self.assertIn("lb-include-changed", self.kinds(self.scan(), "high"))

    def test_include_declared_after_a_higher_number_is_reported(self):
        self.liquibase_base("9999-sample.yaml")
        self.repo.append(MASTER, include("024-widen.yaml"))
        self.repo.write(f"{CHANGES}/024-widen.yaml", "databaseChangeLog:\n  - changeSet:\n      id: 24\n")
        self.repo.commit("add 024")
        report = self.scan()
        order = [f for f in report["findings"] if f["kind"] == "lb-include-order"]
        self.assertEqual(len(order), 1)
        self.assertIn("024-widen.yaml", order[0]["detail"])

    def test_new_change_file_nobody_includes(self):
        self.liquibase_base("001-init.yaml")
        self.repo.write(f"{CHANGES}/002-orphan.yaml", "databaseChangeLog:\n  - changeSet:\n      id: 2\n")
        self.repo.commit("orphan")
        self.assertIn("lb-not-included", self.kinds(self.scan(), "medium"))

    def test_include_all_disables_the_not_included_check(self):
        self.repo.write(MASTER, "databaseChangeLog:\n  - includeAll:\n      path: changes/\n")
        self.repo.write(f"{CHANGES}/001-init.yaml", "databaseChangeLog:\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write(f"{CHANGES}/002-next.yaml", "databaseChangeLog:\n  - changeSet:\n      id: 2\n")
        self.repo.commit("next")
        self.assertNotIn("lb-not-included", self.kinds(self.scan()))


class LiquibaseChangeTypeTest(ScanCase):
    def test_yaml_change_types(self):
        self.liquibase_base("001-init.yaml")
        self.repo.append(MASTER, include("002-risky.yaml"))
        self.repo.write(f"{CHANGES}/002-risky.yaml", (
            "databaseChangeLog:\n  - changeSet:\n      id: 2\n      author: t\n      changes:\n"
            "        - dropTable:\n            tableName: products_deleted\n"
            "        - renameColumn:\n            oldColumnName: a\n            newColumnName: b\n"
            "        - modifyDataType:\n            newDataType: VARCHAR(100)\n"
            "        - addNotNullConstraint:\n            columnName: c\n"
            "        - dropNotNullConstraint:\n            columnName: product_id\n"
            "        - addUniqueConstraint:\n            columnNames: code\n"))
        self.repo.commit("risky")
        kinds = self.kinds(self.scan())
        for kind in ("lb-drop", "lb-rename", "lb-type-change", "lb-not-null", "lb-drop-not-null", "lb-unique",
                     "lb-no-rollback"):
            self.assertIn(kind, kinds)
        self.assertNotIn("migration-existing-changed", kinds)

    def test_xml_change_type(self):
        self.liquibase_base("001-init.yaml")
        self.repo.append(MASTER, include("002-drop.xml"))
        self.repo.write(f"{CHANGES}/002-drop.xml",
                        '<databaseChangeLog>\n<changeSet id="2" author="t">\n'
                        '<dropColumn tableName="t" columnName="c"/>\n</changeSet>\n</databaseChangeLog>\n')
        self.repo.commit("xml")
        self.assertIn("lb-drop", self.kinds(self.scan(), "high"))

    def test_add_column_not_null_without_default(self):
        self.liquibase_base("001-init.yaml")
        self.repo.append(MASTER, include("002-col.yaml"))
        self.repo.write(f"{CHANGES}/002-col.yaml", (
            "databaseChangeLog:\n  - changeSet:\n      id: 2\n      changes:\n        - addColumn:\n"
            "            tableName: t\n            columns:\n              - column:\n"
            "                  name: status\n                  type: varchar(20)\n"
            "                  constraints:\n                    nullable: false\n"))
        self.repo.commit("col")
        self.assertIn("lb-add-not-null", self.kinds(self.scan(), "medium"))

    def test_partial_dbms_conditions(self):
        self.liquibase_base("001-init.yaml")
        self.repo.append(MASTER, include("002-dbms.yaml"))
        self.repo.write(f"{CHANGES}/002-dbms.yaml", (
            "databaseChangeLog:\n  - changeSet:\n      id: a\n      dbms: mariadb\n"
            "  - changeSet:\n      id: b\n      changes:\n        - dropUniqueConstraint:\n"
            "            constraintName: uk\n"))
        self.repo.commit("dbms")
        self.assertIn("lb-dbms-partial", self.kinds(self.scan()))


class RollbackAndNoiseTest(ScanCase):
    def test_rollback_blocks_are_not_what_runs(self):
        self.liquibase_base("001-init.yaml")
        self.repo.append(MASTER, include("025-nullable.yaml"))
        self.repo.write(f"{CHANGES}/025-nullable.yaml", (
            "databaseChangeLog:\n  - changeSet:\n      id: 25\n      author: t\n      changes:\n"
            "        - dropNotNullConstraint:\n            tableName: skus\n            columnName: product_id\n"
            "      rollback:\n        - addNotNullConstraint:\n            tableName: skus\n"
            "        - sql:\n            sql: ALTER TABLE skus DROP COLUMN x\n"
            "  - changeSet:\n      id: 26\n      changes:\n        - dropColumn:\n            columnName: y\n"))
        self.repo.commit("025")
        kinds = self.kinds(self.scan())
        self.assertIn("lb-drop-not-null", kinds)
        self.assertNotIn("lb-not-null", kinds)        # inside rollback
        self.assertNotIn("sql-drop", kinds)           # inside rollback
        self.assertIn("lb-drop", kinds)               # next changeset, after the rollback block
        self.assertNotIn("lb-no-rollback", kinds)

    def test_drop_guarded_by_data_precondition_is_medium(self):
        self.liquibase_base("001-init.yaml")
        self.repo.append(MASTER, include("026-drop.yaml"))
        self.repo.write(f"{CHANGES}/026-drop.yaml", (
            "databaseChangeLog:\n  - changeSet:\n      id: a\n      preConditions:\n        - onFail: MARK_RAN\n"
            "        - sqlCheck:\n            expectedResult: 0\n            sql: SELECT COUNT(*) FROM old_deleted\n"
            "      changes:\n        - dropTable:\n            tableName: old_deleted\n"
            "  - changeSet:\n      id: b\n      changes:\n        - dropTable:\n            tableName: other\n"))
        self.repo.commit("drops")
        report = self.scan()
        guarded = [f for f in report["findings"] if f["kind"] == "lb-drop-guarded"]
        plain = [f for f in report["findings"] if f["kind"] == "lb-drop"]
        self.assertEqual([(f["severity"], f["line"]) for f in guarded], [("medium", 10)])
        self.assertEqual([(f["severity"], f["line"]) for f in plain], [("high", 15)])

    def test_xml_and_formatted_sql_rollback(self):
        self.liquibase_base("001-init.yaml")
        self.repo.append(MASTER, include("002.xml") + include("003.sql"))
        self.repo.write(f"{CHANGES}/002.xml", (
            '<databaseChangeLog>\n<changeSet id="2" author="t">\n<addColumn tableName="t"/>\n'
            '<rollback>\n<dropColumn tableName="t" columnName="c"/>\n</rollback>\n</changeSet>\n'
            '</databaseChangeLog>\n'))
        self.repo.write(f"{CHANGES}/003.sql", (
            "--liquibase formatted sql\n--changeset t:3\nCREATE TABLE n (id INT);\n--rollback DROP TABLE n;\n"))
        self.repo.commit("rollbacks")
        kinds = self.kinds(self.scan())
        self.assertNotIn("lb-drop", kinds)
        self.assertNotIn("sql-drop", kinds)

    def test_drop_index_is_medium_not_data_loss(self):
        self.repo.write(f"{FLYWAY}/V1__init.sql", "CREATE TABLE t (id INT);\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write(f"{FLYWAY}/V2__idx.sql", "DROP INDEX uk_old ON t;\nALTER TABLE t DROP COLUMN c;\n")
        self.repo.commit("drop")
        report = self.scan()
        self.assertEqual([f["line"] for f in report["findings"] if f["kind"] == "sql-drop"], [2])
        self.assertIn("sql-drop-index", self.kinds(report, "medium"))

    def test_yaml_attribute_named_like_a_change_type_is_not_one(self):
        self.liquibase_base("001-init.yaml")
        self.repo.append(MASTER, include("002-sql.yaml"))
        self.repo.write(f"{CHANGES}/002-sql.yaml", (
            "databaseChangeLog:\n  - changeSet:\n      id: 2\n      changes:\n        - sql:\n"
            "            sql: CREATE TABLE z (id INT)\n"))
        self.repo.commit("sql")
        report = self.scan()
        raw = [f for f in report["findings"] if f["kind"] == "lb-raw-sql"]
        self.assertEqual((len(raw), raw[0]["count"]), (1, 1))

    def test_java_rules_skip_tests_and_findings_collapse(self):
        self.repo.write("a.txt", "x\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write("src/test/java/app/ApiTest.java", "class T {\n@GetMapping(\"/x\") void a() {}\n}\n")
        self.repo.write("src/main/java/app/entity/Order.java",
                        "@Entity\nclass Order {\n@Column(name=\"a\") String a;\n@Column(name=\"b\") String b;\n}\n")
        self.repo.commit("java")
        report = self.scan()
        self.assertNotIn("src/test/java/app/ApiTest.java", {f["file"] for f in report["findings"]})
        jpa = [f for f in report["findings"] if f["kind"] == "jpa-mapping"]
        self.assertEqual((len(jpa), jpa[0]["lines"], jpa[0]["count"]), (1, [1, 3, 4], 3))
        self.assertIn("Order.java:1,3,4", pr_scan.render_text(report))


class FlywayTest(ScanCase):
    def setUp(self):
        super().setUp()
        self.repo.write(f"{FLYWAY}/V1__init.sql", "CREATE TABLE orders (id BIGINT);\n")
        self.repo.write(f"{FLYWAY}/V3__more.sql", "CREATE TABLE more (id BIGINT);\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")

    def test_modified_existing_migration_is_high_and_flags_the_commit(self):
        self.repo.write(f"{FLYWAY}/V1__init.sql", "CREATE TABLE orders (id BIGINT, amount DECIMAL);\n")
        self.repo.commit("edit V1")
        report = self.scan()
        self.assertIn("migration-existing-changed", self.kinds(report, "high"))
        self.assertIn("migration-existing-changed", report["commits"][0]["flags"])

    def test_migration_added_then_edited_in_the_same_range_is_not_existing(self):
        self.repo.write(f"{FLYWAY}/V4__new.sql", "CREATE TABLE n (id BIGINT);\n")
        self.repo.commit("add V4")
        self.repo.write(f"{FLYWAY}/V4__new.sql", "CREATE TABLE n (id BIGINT, x INT);\n")
        self.repo.commit("edit V4")
        self.assertNotIn("migration-existing-changed", self.kinds(self.scan()))

    def test_version_lower_than_the_target_branch(self):
        self.repo.write(f"{FLYWAY}/V2__late.sql", "CREATE TABLE late (id BIGINT);\n")
        self.repo.commit("V2")
        self.assertIn("flyway-out-of-order", self.kinds(self.scan(), "medium"))

    def test_duplicate_version(self):
        self.repo.write(f"{FLYWAY}/V3__clash.sql", "CREATE TABLE clash (id BIGINT);\n")
        self.repo.commit("clash")
        self.assertIn("flyway-duplicate-version", self.kinds(self.scan(), "high"))


class DialectTest(ScanCase):
    def base(self, url):
        self.repo.write(PROPS, f"spring.datasource.url={url}\n")
        self.repo.write(f"{FLYWAY}/V1__init.sql", "CREATE TABLE t (id BIGINT);\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")

    def test_mariadb_modify_change_index_and_generated_column(self):
        self.base("jdbc:mariadb://db:3306/app")
        self.repo.write(f"{FLYWAY}/V2__alter.sql", (
            "ALTER TABLE skus MODIFY COLUMN product_id BIGINT NULL;\n"
            "ALTER TABLE skus CHANGE old_col new_col INT;\n"
            "CREATE INDEX idx_a ON skus(a);\n"
            "ALTER TABLE p ADD COLUMN code_active VARCHAR(100) AS (IF(deleted_at IS NULL, code, NULL)) PERSISTENT;\n"
            "CREATE UNIQUE INDEX uk_code ON p (code_active);\n"))
        self.repo.commit("alter")
        report = self.scan()
        self.assertEqual(report["range"]["dialect"], "mariadb")
        type_changes = [f for f in report["findings"] if f["kind"] == "sql-type-change"]
        self.assertEqual(len(type_changes), 1)
        self.assertEqual(type_changes[0]["lines"], [1, 2])
        self.assertIn("sql-generated-column", self.kinds(report, "medium"))
        self.assertIn("sql-unique", self.kinds(report, "medium"))
        index = [f for f in report["findings"] if f["kind"] == "sql-index-lock"]
        self.assertTrue(index and all(f["severity"] == "info" for f in index))
        self.assertNotIn("CONCURRENTLY", " ".join(f["detail"] for f in index))

    def test_postgresql_index_needs_concurrently(self):
        self.base("jdbc:postgresql://db:5432/app")
        self.repo.write(f"{FLYWAY}/V2__idx.sql",
                        "CREATE INDEX idx_a ON t(a);\nCREATE INDEX CONCURRENTLY idx_b ON t(b);\n")
        self.repo.commit("idx")
        report = self.scan()
        self.assertEqual(report["range"]["dialect"], "postgresql")
        locks = [f for f in report["findings"] if f["kind"] == "sql-index-lock"]
        self.assertEqual([(f["severity"], f["line"]) for f in locks], [("medium", 1)])

    def test_dialect_flag_overrides_detection(self):
        self.base("jdbc:mariadb://db:3306/app")
        self.repo.write(f"{FLYWAY}/V2__idx.sql", "CREATE INDEX idx_a ON t(a);\n")
        self.repo.commit("idx")
        report = self.scan(dialect_flag="postgresql")
        self.assertEqual((report["range"]["dialect"], report["range"]["dialect_source"]), ("postgresql", "flag"))

    def test_comments_and_strings_do_not_trigger_rules(self):
        self.base("jdbc:mariadb://db:3306/app")
        self.repo.write(f"{FLYWAY}/V2__data.sql", (
            "-- change the default value of status later\n"
            "INSERT INTO notes (text) VALUES ('please change your pass now; delete from users');\n"
            "# drop table later\n"))
        self.repo.commit("data")
        kinds = self.kinds(self.scan())
        for kind in ("sql-type-change", "sql-delete-no-where", "sql-drop"):
            self.assertNotIn(kind, kinds)

    def test_risky_sql_lines(self):
        self.base("jdbc:mariadb://db:3306/app")
        self.repo.write(f"{FLYWAY}/V2__risk.sql", (
            "ALTER TABLE orders ADD COLUMN status VARCHAR(20) NOT NULL;\n"
            "ALTER TABLE orders ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'A';\n"
            "DROP TABLE legacy_orders;\n"
            "DELETE FROM sessions;\n"
            "UPDATE orders SET status = 'NEW' WHERE status IS NULL;\n"))
        self.repo.commit("risk")
        report = self.scan()
        not_null = [f["line"] for f in report["findings"] if f["kind"] == "sql-add-not-null"]
        self.assertEqual(not_null, [1])
        self.assertIn("sql-drop", self.kinds(report, "high"))
        self.assertIn("sql-delete-no-where", self.kinds(report))
        self.assertNotIn("sql-update-no-where", self.kinds(report))


class ConfigTest(ScanCase):
    def test_kafka_new_group_with_earliest_is_a_replay_risk(self):
        self.repo.write(PROPS, "spring.kafka.consumer.group-id=old\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write(PROPS, "spring.kafka.consumer.group-id=new-group\n"
                               "spring.kafka.consumer.auto-offset-reset=earliest\n"
                               "app.kafka.topics.redemption-event=order_event\n")
        self.repo.commit("kafka")
        report = self.scan()
        self.assertIn("kafka-replay-risk", self.kinds(report, "high"))
        self.assertIn("kafka-group-changed", self.kinds(report, "medium"))
        self.assertIn("kafka-topic-added", self.kinds(report))

    def test_group_without_earliest_is_not_a_replay_risk(self):
        self.repo.write(PROPS, "a=1\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write("src/main/java/app/RedemptionConsumer.java",
                        'class C {\n  @KafkaListener(topics = "t", groupId = "order-event-group")\n'
                        '  void on(String m) {}\n}\n')
        self.repo.commit("listener")
        kinds = self.kinds(self.scan())
        self.assertIn("kafka-listener", kinds)
        self.assertIn("kafka-group-changed", kinds)
        self.assertNotIn("kafka-replay-risk", kinds)

    def test_topic_change_and_removed_key(self):
        self.repo.write(PROPS, "app.import.kafka.topic=import-batch-events\nold.key=1\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write(PROPS, "app.import.kafka.topic=order_command\n")
        self.repo.commit("topic")
        report = self.scan()
        self.assertIn("kafka-topic-changed", self.kinds(report, "medium"))
        removed = [c for c in report["config_changes"] if c["change"] == "bỏ"]
        self.assertEqual([c["key"] for c in removed], ["old.key"])
        self.assertIn("config-key-removed", self.kinds(report))

    def test_secret_value_is_never_printed(self):
        self.repo.write(PROPS, "spring.datasource.password=${DB_PASS}\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write(PROPS, "spring.datasource.password=hunter2secret\n")
        self.repo.commit("oops")
        report = self.scan()
        self.assertIn("config-secret-value", self.kinds(report))
        self.assertNotIn("hunter2", json.dumps(report, ensure_ascii=False))
        self.assertNotIn("hunter2", pr_scan.render_text(report))

    def test_yaml_ddl_auto_and_actuator(self):
        self.repo.write("src/main/resources/application.yml", "spring:\n  jpa:\n    hibernate:\n      ddl-auto: validate\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write("src/main/resources/application.yml",
                        "spring:\n  jpa:\n    hibernate:\n      ddl-auto: create\n"
                        "management:\n  endpoints:\n    web:\n      exposure:\n        include: '*'\n")
        self.repo.commit("yaml")
        report = self.scan()
        self.assertIn("ddl-auto", self.kinds(report, "high"))
        # "include" is a YAML leaf: the key path is not tracked, so it is not reported as actuator exposure.
        self.assertNotIn("actuator-exposed", self.kinds(report))

    def test_properties_actuator_exposure(self):
        self.repo.write(PROPS, "a=1\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write(PROPS, "management.endpoints.web.exposure.include=*\n")
        self.repo.commit("actuator")
        self.assertIn("actuator-exposed", self.kinds(self.scan(), "high"))


class RangeTest(ScanCase):
    def test_report_name_and_metadata(self):
        self.repo.write("README.md", "x\n")
        self.repo.commit("base")
        self.repo.branch("feature/order")
        self.repo.write("pom.xml", "<project><artifactId>x</artifactId></project>\n")
        self.repo.commit("pom")
        report = self.scan(head="feature/order")
        r = report["range"]
        self.assertEqual(r["report_name"], "review-feature-order-to-main-20260101.md")
        self.assertTrue(r["touches_build"])
        self.assertEqual(r["authors"], ["Tester"])
        self.assertRegex(r["first_date"], r"^\d{4}-\d{2}-\d{2}$")
        named = self.scan(head="feature/order", pr="13")
        self.assertEqual(named["range"]["report_name"], "review-pr-13-service-20260101.md")

    def test_merge_check_clean_and_conflict(self):
        self.repo.write("a.txt", "one\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write("a.txt", "feature\n")
        self.repo.commit("feature edit")
        self.assertEqual(self.scan()["range"]["merge"]["status"], "clean")
        self.repo.checkout("main")
        self.repo.write("a.txt", "main\n")
        self.repo.commit("main edit")
        merge = self.scan()["range"]["merge"]
        self.assertEqual((merge["status"], merge["conflicts"]), ("conflict", ["a.txt"]))
        self.assertEqual(self.scan(merge=False)["range"]["merge"]["status"], "skipped")

    def test_bad_ref_exits_2(self):
        self.repo.write("a.txt", "one\n")
        self.repo.commit("base")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pr_scan.main(["--repo", self.repo.path, "--base", "nope", "--head", "main"])
        self.assertEqual(code, 2)
        self.assertIn("không tìm thấy ref: nope", err.getvalue())

    def test_patch_file_folder_is_created(self):
        self.repo.write("a.txt", "one\n")
        self.repo.commit("base")
        self.repo.branch("feature/x")
        self.repo.write("a.txt", "two\n")
        self.repo.commit("two")
        target = self.repo.parent + "/out/deep/pr.patch"
        with contextlib.redirect_stdout(io.StringIO()):
            code = pr_scan.main(["--repo", self.repo.path, "--base", "main", "--head", "feature/x",
                                 "--patch-file", target])
        self.assertEqual(code, 0)
        with open(target, encoding="utf-8") as fh:
            self.assertIn("+two", fh.read())


class CategorizeTest(unittest.TestCase):
    def test_categories(self):
        c = pr_scan.categorize
        self.assertNotIn("security", c("src/main/java/app/AuthorService.java"))
        self.assertIn("security", c("src/main/java/app/config/SecurityConfig.java"))
        self.assertIn("security", c("src/main/java/app/web/JwtFilter.java"))
        self.assertIn("api", c("src/main/java/app/OrderController.java"))
        self.assertNotIn("persistence", c("src/main/java/app/model/Order.java"))
        self.assertIn("persistence", c("src/main/java/app/entity/Order.java"))
        self.assertIn("migration", c("src/main/resources/db.changelog-master.yaml"))
        self.assertIn("migration", c("src/main/resources/db/migration/V2_1__x.sql"))
        self.assertEqual(c("src/test/java/app/OrderControllerTest.java"), ["test"])
        self.assertEqual(c("docs/migrations.md"), ["other"])


if __name__ == "__main__":
    unittest.main()
