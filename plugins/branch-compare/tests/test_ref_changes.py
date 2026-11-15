import contextlib
import io
import json
import os
import tempfile
import unittest

import helpers  # noqa: F401  (isolated settings, script path)
from helpers import Repo

import java_inventory as ji  # noqa: E402
import ref_changes as rc  # noqa: E402

J = "src/main/java/com/shop/order"
RES = "src/main/resources"
LB = f"{RES}/db/changelog"

CONTROLLER_V1 = """package com.shop.order.web;
@RestController
@RequestMapping("/api/v1/orders")
public class OrderController {
    @PostMapping
    public OrderResponse create(@RequestHeader("X-Trace") String trace, @Valid @RequestBody CreateOrderRequest req) {
        return null;
    }
    @GetMapping("/{id}")
    public OrderResponse get(@PathVariable String id) { return null; }
    @DeleteMapping("/{id}")
    public void cancel(@PathVariable String id) {}
}
"""
CONTROLLER_V2 = """package com.shop.order.web;
@RestController
@RequestMapping("/api/v1/orders")
public class OrderController {
    private static final String CLIENT = "X-Client-Authorization";
    @PostMapping
    public OrderResponse create(@RequestHeader("X-Trace") String trace, @RequestHeader(CLIENT) String client,
                                @Valid @RequestBody CreateOrderRequest req) {
        return null;
    }
    @GetMapping("/{orderId}")
    public OrderResponse get(@PathVariable String orderId) { return null; }
    @PostMapping("/{id}/confirm")
    @PreAuthorize("hasRole('SYSTEM')")
    public OrderResponse confirm(@PathVariable String id) { return null; }
}
"""
REQUEST_V1 = """package com.shop.order.dto;
public class CreateOrderRequest {
    private String customerId;
    private String channel;
    private Long amount;
}
"""
REQUEST_V2 = """package com.shop.order.dto;
public class CreateOrderRequest {
    private String customerId;
    @NotBlank private String serviceCode;
    private java.math.BigDecimal amount;
}
"""
INTERNAL_V1 = "package com.shop.order.dto;\npublic class PriceCalcResult { private int total; }\n"
INTERNAL_V2 = "package com.shop.order.dto;\npublic class PriceCalcResult { private long total; }\n"
ENTITY_V1 = """package com.shop.order.entity;
@Entity
@Table(name = "orders")
public class OrderEntity {
    @Id private Long id;
    @Column(name = "group_type", length = 20) private String groupType;
    @Enumerated(EnumType.STRING) private OrderStatus status;
}
"""
ENTITY_V2 = """package com.shop.order.entity;
@Entity
@Table(name = "orders")
public class OrderEntity {
    @Id private Long id;
    @Enumerated(EnumType.STRING) private OrderStatus status;
    private String serviceCode;
    private String note;
}
"""
YAML_V1 = """app:
  name: order
  topics:
    created: order_created
  allowed:
    - a
    - b
spring:
  datasource:
    url: jdbc:mariadb://db:3306/order
    password: s3cr3t-value
---
spring:
  config:
    activate:
      on-profile: uat
app:
  name: order-uat
"""
YAML_V2 = """app:
  name: order
  topics:
    created: order_created_v2
    archived: order_archived
  allowed:
    - a
spring:
  datasource:
    url: jdbc:mariadb://user:pw@db:3306/order
    password: other-secret
---
spring:
  config:
    activate:
      on-profile: uat
app:
  name: order-uat2
"""
MASTER = """<databaseChangeLog>
  <include file="changes/001-init.xml" relativeToChangelogFile="true"/>
</databaseChangeLog>
"""
INIT = """<databaseChangeLog>
  <changeSet id="001" author="a">
    <createTable tableName="orders">
      <column name="id" type="BIGINT"/>
      <column name="group_type" type="VARCHAR(20)"/>
      <column name="status" type="VARCHAR(20)"/>
    </createTable>
  </changeSet>
</databaseChangeLog>
"""
MIG_002 = """<databaseChangeLog>
  <changeSet id="002" author="a" context="!test">
    <addColumn tableName="orders">
      <column name="service_code" type="VARCHAR(50)"/>
    </addColumn>
    <sql>DELETE FROM orders WHERE group_type &lt;&gt; 'MY';</sql>
    <rollback>
      <dropColumn tableName="orders" columnName="service_code"/>
    </rollback>
  </changeSet>
  <changeSet id="003" author="a">
    <dropColumn tableName="orders" columnName="group_type"/>
  </changeSet>
</databaseChangeLog>
"""
POM_V1 = """<project><properties><jwt.version>9.0</jwt.version></properties>
<dependencies>
  <dependency><groupId>com.nimbusds</groupId><artifactId>nimbus-jose-jwt</artifactId><version>${jwt.version}</version></dependency>
  <dependency><groupId>org.old</groupId><artifactId>gone</artifactId><version>1.0</version></dependency>
</dependencies></project>
"""
POM_V2 = """<project><properties><jwt.version>10.9.1</jwt.version></properties>
<dependencies>
  <dependency><groupId>com.nimbusds</groupId><artifactId>nimbus-jose-jwt</artifactId><version>${jwt.version}</version></dependency>
  <dependency><groupId>org.new</groupId><artifactId>lib</artifactId><version>2.0-SNAPSHOT</version></dependency>
</dependencies></project>
"""


class ChangesCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = cls.repo = Repo(branch="main")
        r.write(f"{J}/web/OrderController.java", CONTROLLER_V1)
        r.write(f"{J}/dto/CreateOrderRequest.java", REQUEST_V1)
        r.write(f"{J}/dto/OrderResponse.java", "package x;\npublic class OrderResponse { private String id; }\n")
        r.write(f"{J}/dto/PriceCalcResult.java", INTERNAL_V1)
        r.write(f"{J}/entity/OrderEntity.java", ENTITY_V1)
        r.write(f"{J}/entity/OrderStatus.java", "package x;\npublic enum OrderStatus { NEW, PAID, CANCELLED }\n")
        r.write(f"{J}/job/CleanupJob.java",
                "package x;\npublic class CleanupJob {\n  @Scheduled(cron = \"0 0 1 * * *\")\n  public void run() {}\n}\n")
        r.write(f"{RES}/application.yml", YAML_V1)
        r.write(f"{LB}/db.changelog-master.xml", MASTER)
        r.write(f"{LB}/changes/001-init.xml", INIT)
        r.write("pom.xml", POM_V1)
        r.commit("init")
        r.git("tag", "v1.0.0")
        r.write("README.md", "docs\n")
        r.commit("docs")
        r.git("tag", "v1.0.1")
        r.write(f"{J}/web/OrderController.java", CONTROLLER_V2)
        r.write(f"{J}/dto/CreateOrderRequest.java", REQUEST_V2)
        r.write(f"{J}/dto/PriceCalcResult.java", INTERNAL_V2)
        r.commit("ORD-12 serviceCode bắt buộc khi tạo đơn")
        r.write(f"{J}/entity/OrderEntity.java", ENTITY_V2)
        r.write(f"{J}/entity/OrderStatus.java", "package x;\npublic enum OrderStatus { NEW, PAID }\n")
        r.write(f"{LB}/changes/002-service-code.xml", MIG_002)
        r.write(f"{LB}/db.changelog-master.xml",
                MASTER.replace("</databaseChangeLog>",
                               '  <include file="changes/002-service-code.xml" relativeToChangelogFile="true"/>\n'
                               "</databaseChangeLog>"))
        r.commit("ORD-13 bỏ group_type")
        r.write(f"{J}/job/CleanupJob.java",
                "package x;\npublic class CleanupJob {\n  @Scheduled(cron = \"0 0 2 * * *\")\n  public void run() {}\n}\n")
        r.write(f"{RES}/application.yml", YAML_V2)
        r.write("pom.xml", POM_V2)
        r.write(f"{J}/mongock/ChangeLog007.java",
                "@ChangeUnit(id = \"007\", order = \"007\")\nclass ChangeLog007 {\n  @Execution void run(MongoTemplate t) {\n"
                "    t.getCollection(\"session\").createIndex(new Document(\"orderId\", 1));\n  }\n}\n")
        r.write("src/test/java/com/shop/order/FakeEndpoints.java",
                "@RestController\nclass FakeEndpoints {\n  @GetMapping(\"/test-only\")\n  String x() { return null; }\n}\n")
        r.commit("ORD-14 config, job, deps")
        r.git("tag", "v1.1.0")
        cls.report = rc.changes(r.path, "v1.0.0", "v1.1.0", today="20260101")

    @classmethod
    def tearDownClass(cls):
        cls.repo.cleanup()


class RangeTest(ChangesCase):
    def test_range_commits_and_report_name(self):
        g = self.report["range"]
        self.assertEqual((g["from_kind"], g["to_kind"]), ("tag", "tag"))
        self.assertTrue(g["from_is_ancestor"])
        self.assertEqual(g["counts"]["commits"], 4)
        self.assertEqual(g["report_name"], "changes-v1.0.0-to-v1.1.0-order-service-20260101.md")
        self.assertEqual({t["ticket"] for t in self.report["tickets"]},
                         {"ORD-12", "ORD-13", "ORD-14", "(không ticket)"})

    def test_auto_from_is_the_previous_tag(self):
        r = rc.changes(self.repo.path, "auto", "v1.1.0", today="20260101")
        self.assertEqual(r["range"]["from_ref"], "v1.0.1")
        self.assertEqual(r["range"]["counts"]["commits"], 3)

    def test_unknown_ref_and_same_commit_are_errors(self):
        with self.assertRaises(rc.ps.ScanError):
            rc.changes(self.repo.path, "v0.9", "v1.1.0")
        with self.assertRaises(rc.ps.ScanError):
            rc.changes(self.repo.path, "v1.1.0", "v1.1.0")


class ApiTest(ChangesCase):
    def endpoint(self, change, path):
        return next(e for e in self.report["api"]["endpoints"] if e["change"] == change and e["path"] == path)

    def test_added_removed_and_changed_endpoints(self):
        self.endpoint("added", "/api/v1/orders/{id}/confirm")
        self.endpoint("removed", "/api/v1/orders/{id}")   # DELETE
        create = self.endpoint("changed", "/api/v1/orders")
        self.assertIn("X-Client-Authorization", create["headers"])          # constant resolved
        self.assertTrue(any("X-Client-Authorization" in b for b in create["breaking"]))

    def test_controllers_in_test_sources_are_ignored(self):
        self.assertFalse([e for e in self.report["api"]["endpoints"] if e["path"] == "/test-only"])

    def test_renaming_a_path_variable_is_a_change_not_add_and_remove(self):
        get = [e for e in self.report["api"]["endpoints"] if e["method"] == "GET"]
        self.assertEqual([e["change"] for e in get], ["changed"])
        self.assertIn("path_vars", {d["field"] for d in get[0]["differences"]})

    def test_request_fields_and_exposure(self):
        rows = {d["class"]: d for d in self.report["api"]["data_classes"]}
        req = rows["CreateOrderRequest"]
        self.assertTrue(req["exposed"])
        self.assertIn("POST /api/v1/orders", req["used_by"])
        self.assertEqual([f["field"] for f in req["added"]], ["serviceCode"])
        self.assertEqual([f["field"] for f in req["removed"]], ["channel"])
        self.assertTrue(any("serviceCode" in b for b in req["breaking"]))
        self.assertTrue(any("amount" in b for b in req["breaking"]))
        internal = rows["PriceCalcResult"]
        self.assertFalse(internal["exposed"])
        self.assertEqual(internal["breaking"], [])
        self.assertTrue(internal["internal_changes"])


class BusinessTest(ChangesCase):
    def test_enum_and_scheduled_job(self):
        b = self.report["business"]
        enum = next(x for x in b["enums"] if x["name"] == "OrderStatus")
        self.assertEqual(enum["new"], ["NEW", "PAID"])
        job = next(x for x in b["scheduled"] if x["name"] == "CleanupJob#run")
        self.assertIn("0 0 2", job["new"][0])

    def test_persisted_enum_constant_removed_is_high(self):
        f = [x for x in self.report["findings"] if x["kind"] == "enum-persisted-constant-removed"]
        self.assertEqual(len(f), 1)
        self.assertIn("CANCELLED", f[0]["detail"])


class ConfigTest(ChangesCase):
    def rows(self):
        return {x["key"]: x for x in self.report["config"]["packaged"]}

    def test_values_before_and_after_with_profiles_and_lists(self):
        rows = self.rows()
        self.assertEqual((rows["app.topics.created"]["old"], rows["app.topics.created"]["new"]),
                         ("order_created", "order_created_v2"))
        self.assertEqual(rows["app.topics.archived"]["change"], "added")
        self.assertEqual(rows["app.allowed[1]"]["change"], "removed")
        self.assertEqual(rows["app.name @uat"]["new"], "order-uat2")
        self.assertNotIn("app.name", rows)

    def test_secrets_and_url_passwords_are_masked(self):
        rows = self.rows()
        self.assertEqual(rows["spring.datasource.password"]["old"], "<secret>")
        self.assertEqual(rows["spring.datasource.password"]["new"], "<secret>")
        self.assertIn("user:***@", rows["spring.datasource.url"]["new"])
        dumped = json.dumps(self.report)
        for secret in ("s3cr3t-value", "other-secret", "user:pw@"):
            self.assertNotIn(secret, dumped)

    def test_copy_ready_snippet(self):
        snip = self.report["config"]["snippet"]
        self.assertIn("# ===== src/main/resources/application.yml =====", snip)
        self.assertIn("app.topics.archived=order_archived", snip)
        self.assertIn("app.topics.created=order_created_v2    # cũ: order_created", snip)
        self.assertIn("app.name=order-uat2    # profile uat; cũ: order-uat", snip)
        self.assertIn("# app.allowed[1]", snip)
        self.assertNotIn("other-secret", snip)

    def test_snippet_folds_a_removed_list_item_but_not_one_still_partly_set(self):
        rows = [{"file": "a.properties", "change": "removed", "key": k, "old": "x", "new": None}
                for k in ("t[0].f[0].op", "t[0].f[0].v", "t[2].code", "t[2].f[0].op")]
        rows.append({"file": "a.properties", "change": "added", "key": "need", "new": "${NEED_ME}", "old": None})
        snip = rc.config_snippet(rows, [{"key": "NEED_ME", "packaged_in": None}],
                                 {"a.properties": ["t[0].code", "need"]})
        self.assertIn("# t[0].f[0].*", snip)
        self.assertIn("# t[2].*", snip)
        self.assertNotIn("# t[0].*", snip)
        self.assertNotIn("t[2].f[0]", snip)
        self.assertTrue(snip.rstrip().endswith("NEED_ME="))

    def test_topics_now_for_cross_service_links(self):
        self.assertEqual(self.report["kafka"]["topics_now"]["app.topics.archived"], "order_archived")


class DatabaseTest(ChangesCase):
    def test_migration_operations_without_rollback_blocks(self):
        mig = next(m for m in self.report["database"]["migrations"] if m["file"].endswith("002-service-code.xml"))
        ops = [(o["op"], o["table"], o.get("column")) for o in mig["ops"]]
        self.assertIn(("add_column", "orders", "service_code"), ops)
        self.assertIn(("delete_data", "orders", None), ops)
        self.assertIn(("drop_column", "orders", "group_type"), ops)
        self.assertNotIn(("drop_column", "orders", "service_code"), ops)    # rollback only
        self.assertEqual([c["context"] for c in mig["changesets"]], ["!test", None])
        self.assertTrue(mig["changesets"][0]["rollback"])

    def test_mongock_change_units_count_as_migrations(self):
        rows = [m for m in self.report["database"]["migrations"] if m.get("kind") == "mongock"]
        self.assertEqual([m["file"].rsplit("/", 1)[1] for m in rows], ["ChangeLog007.java"])
        self.assertIn("createIndex", rows[0]["ops"][0]["detail"])

    def test_entity_columns_and_migration_coverage(self):
        e = next(x for x in self.report["database"]["entities"] if x["entity"] == "OrderEntity")
        self.assertEqual([c["column"] for c in e["added"]], ["note", "service_code"])
        self.assertEqual([c["column"] for c in e["removed"]], ["group_type"])
        missing = [x["detail"] for x in self.report["findings"] if x["kind"] == "entity-column-without-migration"]
        self.assertEqual(len(missing), 1)
        self.assertIn("orders.note", missing[0])

    def test_a_column_created_by_an_older_migration_is_not_reported(self):
        rows = [{"entity": "E", "change": "changed", "table": "orders", "file": "E.java",
                 "added": [{"column": "status", "field": "status"}, {"column": "fresh", "field": "fresh"}]}]
        out = rc.consistency({}, rows, [], [], True, known=lambda name: name == "status")
        self.assertEqual([x["detail"].split(" ")[0] for x in out], ["E.fresh"])
        self.assertFalse([x for x in self.report["findings"] if x["kind"] == "migration-drops-mapped-column"])


class DependencyTest(ChangesCase):
    def test_versions_through_properties_snapshot_and_removed(self):
        rows = {x["name"]: x for x in self.report["dependencies"]}
        jwt = rows["com.nimbusds:nimbus-jose-jwt"]
        self.assertEqual((jwt["old"], jwt["new"]), ("9.0", "10.9.1"))
        self.assertTrue(rows["org.new:lib"]["snapshot"])
        self.assertEqual(rows["org.old:gone"]["change"], "removed")


class CliTest(ChangesCase):
    def test_text_output_and_files(self):
        out = tempfile.mkdtemp(prefix="rc-out-")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rc.main(["--repo", self.repo.path, "--from", "v1.0.0", "--to", "v1.1.0", "--format", "text",
                            "--out", os.path.join(out, "c.json"), "--patch-file", os.path.join(out, "c.patch")])
        self.assertEqual(code, 0)
        self.assertIn("Endpoints:", buf.getvalue())
        self.assertTrue(os.path.getsize(os.path.join(out, "c.patch")) > 0)
        with open(os.path.join(out, "c.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["range"]["to_ref"], "v1.1.0")

    def test_bad_ref_exits_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rc.main(["--repo", self.repo.path, "--from", "nope", "--to", "v1.1.0"]), 2)
        self.assertIn("nope", err.getvalue())

    def test_discover_lists_repositories_with_both_refs(self):
        rows = rc.discover(self.repo.parent, "v1.0.0", "v1.1.0")
        self.assertEqual([(x["repo"], x["status"], x["commits"]) for x in rows], [("order-service", "ok", 4)])


class CrossTest(unittest.TestCase):
    def test_changed_endpoint_called_by_another_service_and_shared_topic(self):
        provider = {"range": {"repo": "redemption"},
                    "api": {"endpoints": [{"kind": "http", "change": "changed", "method": "POST",
                                           "path": "/${ctx}/v1/session/{id}/confirm", "breaking": ["body đổi"],
                                           "differences": [{"field": "body"}]}], "feign_now": []},
                    "config": {"packaged": [{"key": "app.topics.done", "change": "added", "old": None,
                                             "new": "session_done"}]},
                    "kafka": {"topics_now": {"app.topics.done": "session_done"}}}
        consumer = {"range": {"repo": "bff"},
                    "api": {"endpoints": [], "feign_now": [{"client": "redemption", "method": "POST",
                                                            "path": "/${x.path}/v1/session/{sessionId}/confirm",
                                                            "handler": "RedemptionClient#confirm"}]},
                    "config": {"packaged": []},
                    "kafka": {"topics_now": {"bff.topics.session-done": "session_done"}}}
        rows = rc.cross([provider, consumer])
        self.assertEqual({(r["kind"], r["provider"], r["consumer"]) for r in rows},
                         {("api", "redemption", "bff"), ("topic", "redemption", "bff")})
        self.assertIn("| api | redemption | bff |", rc.render_cross(rows))


class JavaInventoryTest(unittest.TestCase):
    def test_comments_strings_lambdas_and_nested_classes(self):
        src = """
        /* @GetMapping("/commented") */
        @RestController
        class C {
            // @PostMapping("/also-commented")
            private final Runnable r = () -> { String s = "}{;"; };
            @GetMapping(value = {"/a", "/b"})
            String list(@RequestParam(name = "q", required = false) String q) { if (q != null) { return "}"; } return ""; }
            static class Inner { @NotNull private Long id; }
        }
        """
        types = ji.parse_java(src)
        self.assertEqual([t["name"] for t in types], ["C", "C.Inner"])
        eps = ji.endpoints(types, "C.java")
        self.assertEqual(sorted(e["path"] for e in eps), ["/a", "/b"])
        self.assertEqual(eps[0]["params"], ["q?"])

    def test_feign_client_with_path_and_record_dto(self):
        src = """
        @FeignClient(name = "pricing", path = "/internal")
        public interface PricingClient {
            @PostMapping("/price")
            PriceResponse price(@RequestHeader("X-Key") String key, @RequestBody PriceRequest body);
        }
        """
        e = ji.endpoints(ji.parse_java(src), "PricingClient.java")[0]
        self.assertEqual((e["kind"], e["client"], e["path"], e["headers"]), ("feign", "pricing", "/internal/price",
                                                                            ["X-Key"]))
        rec = ji.parse_java("public record PriceRequest(@NotBlank String code, @JsonProperty(\"amt\") Long amount) {}")
        fields = ji.data_fields(rec[0])
        self.assertTrue(fields["code"]["required"])
        self.assertEqual(fields["amount"]["json"], ["amt"])

    def test_entity_default_naming_and_relations(self):
        src = """
        @Entity public class CampaignOffer {
            @Id private Long id;
            private String customerId;
            @ManyToOne private Campaign campaign;
            @OneToMany(mappedBy = "offer") private List<Item> items;
            @Transient private String tmp;
            private static final long X = 1;
        }
        """
        t = ji.parse_java(src)[0]
        table, cols = ji.entity_columns(t)
        self.assertEqual(table, "campaign_offer")
        self.assertEqual(sorted(cols), ["campaign_id", "customer_id", "id"])


class SqlOpsTest(unittest.TestCase):
    def test_mariadb_statements(self):
        ops = rc.sql_ops("""
            CREATE TABLE IF NOT EXISTS a_archive LIKE a;
            ALTER TABLE a_archive DROP PRIMARY KEY, ADD COLUMN archived_at DATETIME(6) NOT NULL, MODIFY x DECIMAL(10,2);
            UPDATE a SET s = 'x;y';
            DELETE FROM a WHERE id = 1;
            RENAME TABLE a TO b;
        """)
        self.assertEqual([o["op"] for o in ops], ["create_table", "drop_constraint", "add_column", "modify_column",
                                                  "update_data", "delete_data", "rename_table"])
        self.assertEqual(ops[4]["detail"], "không có WHERE")
        self.assertNotIn("detail", ops[5])

    def test_yaml_changelog_with_an_sql_block(self):
        text = """databaseChangeLog:
  - changeSet:
      id: 088-drop-unused-columns
      author: dev
      changes:
        - sql:
            splitStatements: true
            sql: |
              ALTER TABLE rule_bindings DROP COLUMN IF EXISTS product_scope, DROP COLUMN IF EXISTS enabled;
              DROP TABLE IF EXISTS publish_jobs;
      rollback:
        - sql:
            sql: SELECT 1;
"""
        ops, sets = rc.migration_ops("db/changelog/088-drop.yaml", text)
        self.assertEqual([(o["op"], o["table"], o.get("column")) for o in ops],
                         [("drop_column", "rule_bindings", "product_scope"), ("drop_column", "rule_bindings", "enabled"),
                          ("drop_table", "publish_jobs", None)])
        self.assertEqual((sets[0]["id"], sets[0]["rollback"]), ("088-drop-unused-columns", True))

    def test_formatted_sql_changesets(self):
        ops, sets = rc.migration_ops("db/changelog/003.sql",
                                     "--liquibase formatted sql\n--changeset dev:3 context:prod\n"
                                     "ALTER TABLE t ADD c INT;\n--rollback ALTER TABLE t DROP c;\n")
        self.assertEqual([(o["op"], o["column"]) for o in ops], [("add_column", "c")])
        self.assertEqual((sets[0]["id"], sets[0]["context"], sets[0]["rollback"]), ("3", "prod", True))


if __name__ == "__main__":
    unittest.main()
