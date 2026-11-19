import contextlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "sql-runner", "scripts"))
import plugin_settings  # noqa: E402
import sql_runner  # noqa: E402

PASSWORD = "pw-secret-123"
TOKEN = "jwt-token-xyz"


class FakeTool(http.server.BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def do_GET(self):
        FakeTool.calls.append(("GET", self.path, None))
        if self.path == "/vms/api/wa/connection-info/get-all-public" and self.authed():
            return self.reply(200, {"data": [{"id": 7, "name": "UAT_db", "dbType": "MARIADB", "connectionString": "jdbc:x://u:p@h"},
                                             {"id": 9, "name": "PROD_db", "dbType": "MARIADB"},
                                             {"id": 11, "name": " product_service_view", "dbType": "mysql"},
                                             {"id": 12, "name": "core_ora", "dbType": "oracle"}]})
        self.reply(401, {"status": 401, "msg": "unauthorized"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeTool.calls.append(("POST", self.path, body))
        if self.path == "/vms/api/auth/login":
            if body == {"username": "me", "password": PASSWORD}:
                return self.reply(200, {"data": {"token": TOKEN}})
            return self.reply(200, {"status": 401, "msg": f"bad credentials for {body.get('password')}"})
        if self.path == "/vms/api/wa/sql-runner/execute" and self.authed():
            q = body["query"].lower()
            if q.startswith("select"):
                return self.reply(200, {"data": {"columns": ["id", "name"], "rows": [[1, "a"], [2, None], [3, "c\nd"]]}})
            if q.startswith("show create"):
                return self.reply(200, {"data": {"columns": [{"name": "Table"}, {"name": "Create Table"}],
                                                 "rows": [{"Table": "t", "Create Table": "CREATE TABLE `t` (...)"}]}})
            if q.startswith("insert"):
                if body.get("affectedRows") != 1:
                    return self.reply(200, {"status": 400, "msg": "Operation affects different number of rows"})
                return self.reply(200, {"data": {"affectedRows": 1}})
            return self.reply(200, {"data": {"message": "ok"}})
        self.reply(401, {"status": 401, "msg": "unauthorized"})


class SqlRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeTool)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeTool.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SQL_RUNNER_", "VMS_SQL_RUNNER_"))}
        env.update({"DEV_CLI_HOME": self.tmp.name, "SQL_RUNNER_URL": f"http://127.0.0.1:{self.server.server_port}",
                    "SQL_RUNNER_USER": "me", "SQL_RUNNER_PASSWORD": PASSWORD,
                    "SQL_RUNNER_WRITE_CONNECTIONS": "uat_*,prod_*"})
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        plugin_settings._SETTINGS = None

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sql_runner.main(list(argv))
        text = out.getvalue() + err.getvalue()
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(TOKEN, text)
        return code, out.getvalue(), err.getvalue()

    def executed(self):
        return [b for m, p, b in FakeTool.calls if p.endswith("/execute")]

    def test_select_prints_a_table(self):
        code, out, _ = self.run_cli("query", "select id, name from s.t", "--conn", "UAT_db")
        self.assertEqual(code, 0)
        self.assertIn("id | name", out)
        self.assertIn("2  | NULL", out)
        self.assertIn("c\\nd", out)
        self.assertIn("(3 dòng)", out)
        self.assertEqual(self.executed()[0]["database"], {"id": "7", "name": ""})
        self.assertNotIn("affectedRows", self.executed()[0])

    def test_max_rows_json_and_out_file(self):
        code, out, _ = self.run_cli("query", "select 1", "--conn", "UAT_db", "--max-rows", "2", "--format", "json")
        self.assertEqual(len(json.loads(out.split("\n...")[0])), 2)
        path = os.path.join(self.tmp.name, "rows.csv")
        code, out, _ = self.run_cli("query", "select 1", "--conn", "UAT_db", "--out", path)
        self.assertEqual(code, 0)
        with open(path) as fh:
            self.assertEqual(fh.readline().strip(), "id,name")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        code, _, err = self.run_cli("query", "select 1", "--conn", "UAT_db", "--out", path)
        self.assertEqual(code, 2)

    def test_writes_are_refused_by_default(self):
        for sql in ("insert into s.t values (1)", "create table s.x (a int)", "select * from s.t for update",
                    "grant all on *.* to x"):
            code, _, err = self.run_cli("query", sql, "--conn", "UAT_db")
            self.assertEqual(code, 2, sql)
        self.assertEqual(self.executed(), [])

    def code(self, conn, sql, rows, kind):
        return sql_runner.confirm_code(conn, sql, rows, kind)

    def test_write_needs_affected_rows_and_the_confirmation_code(self):
        code, _, err = self.run_cli("query", "insert into s.t values (1)", "--conn", "UAT_db", "--write")
        self.assertEqual(code, 2)
        self.assertIn("--affected-rows", err)
        code, out, _ = self.run_cli("query", "insert into s.t values (1)", "--conn", "UAT_db", "--write", "--affected-rows", "1")
        self.assertEqual(code, 2)
        self.assertIn("CHƯA CHẠY", out)
        shown = out.strip().splitlines()[-1].split()[-1]
        self.assertEqual(shown, self.code("UAT_db", "insert into s.t values (1)", 1, "dml"))
        self.assertEqual(len(shown), 6)
        # a code for another statement, another row count or a guess is refused
        for argv in (("insert into s.t values (2)", "--affected-rows", "1", "--confirm", shown),
                     ("insert into s.t values (1)", "--affected-rows", "2", "--confirm", shown),
                     ("insert into s.t values (1)", "--affected-rows", "1", "--confirm", "ABCDEF")):
            code, _, err = self.run_cli("query", argv[0], "--conn", "UAT_db", "--write", *argv[1:])
            self.assertEqual(code, 2)
            self.assertIn("không khớp", err)
        self.assertEqual(self.executed(), [])
        self.assertNotEqual(shown, self.code("UAT_other", "insert into s.t values (1)", 1, "dml"))
        code, out, _ = self.run_cli("query", "insert  into s.t\n values (1)", "--conn", "UAT_db", "--write",
                                    "--affected-rows", "1", "--confirm", shown.lower())
        self.assertEqual(code, 0)  # same statement (spacing ignored), code typed in lower case
        self.assertIn("số dòng bị ảnh hưởng: 1", out)
        self.assertEqual(self.executed()[0]["affectedRows"], 1)

    def test_tool_error_is_reported(self):
        code, _, err = self.run_cli("query", "insert into s.t values (1)", "--conn", "UAT_db", "--write",
                                    "--affected-rows", "2", "--confirm",
                                    self.code("UAT_db", "insert into s.t values (1)", 2, "dml"))
        self.assertEqual(code, 6)
        self.assertIn("different number of rows", err)

    def test_production_needs_allow_prod(self):
        code, _, err = self.run_cli("query", "create table s.x (a int)", "--conn", "PROD_db", "--write", "--confirm", "ABCDEF")
        self.assertEqual(code, 2)
        self.assertIn("production", err)
        code, _, _ = self.run_cli("query", "create table s.x (a int)", "--conn", "PROD_db", "--write", "--allow-prod",
                                    "--confirm", self.code("PROD_db", "create table s.x (a int)", None, "ddl"))
        self.assertEqual(code, 0)
        os.environ["SQL_RUNNER_WRITE_CONNECTIONS"] = "dev_*"
        code, _, err = self.run_cli("query", "create table s.y (a int)", "--conn", "UAT_db", "--write", "--confirm", "ABCDEF")
        self.assertEqual(code, 2)
        self.assertIn("SQL_RUNNER_WRITE_CONNECTIONS", err)

    def test_lint_blocks_known_rejections_before_sending(self):
        for sql in ("drop view s.v", "create sequence s.q", "insert into s.t values ('can\\'t')",
                    "create definer=`x`@`%` sql security definer view s.v as select 1",
                    "create table s.p (a int) partition by range (a) (partition p0 values less than (10))"):
            code, _, err = self.run_cli("query", sql, "--conn", "UAT_db", "--write", "--affected-rows", "1", "--confirm", "ABCDEF")
            self.assertEqual(code, 2, sql)
            self.assertIn("chặn", err)
        self.assertEqual(self.executed(), [])
        self.assertEqual(sql_runner.lint("insert into s.t values ('can''t')"), [])

    def test_one_statement_per_argument(self):
        code, _, err = self.run_cli("query", "select 1; select 2", "--conn", "UAT_db")
        self.assertEqual(code, 2)
        self.assertEqual(sql_runner.split_statements("select ';' ; -- x;\n/* ; */ select `a;b`"),
                         ["select ';'", "select `a;b`"])
        code, out, _ = self.run_cli("query", "select 1", "select 2;", "--conn", "UAT_db")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.executed()), 2)

    def test_unknown_connection_lists_names(self):
        code, _, err = self.run_cli("query", "select 1", "--conn", "nope")
        self.assertEqual(code, 4)
        self.assertIn("connections --filter", err)
        code, _, err = self.run_cli("query", "select 1", "--conn", "uat_db_x")
        self.assertEqual(code, 4)
        self.assertIn("Gần giống: UAT_db", err)

    def test_connections_hide_connection_strings(self):
        code, out, _ = self.run_cli("connections")
        self.assertEqual(code, 0)
        self.assertNotIn("jdbc", out)
        self.assertRegex(out, r"PROD_db +\| MARIADB +\| production +\| +\n")
        self.assertRegex(out, r"UAT_db +\| MARIADB +\| +\| ghi được")
        self.assertRegex(out, r"product_service_view +\| mysql +\| +\| +\n")
        code, out, _ = self.run_cli("connections", "--filter", "uat")
        self.assertIn("(1 connection)", out)

    def test_writes_need_an_allow_list(self):
        del os.environ["SQL_RUNNER_WRITE_CONNECTIONS"]
        code, _, err = self.run_cli("query", "create table s.x (a int)", "--conn", "UAT_db", "--write", "--confirm", "ABCDEF")
        self.assertEqual(code, 2)
        self.assertIn("chưa đặt SQL_RUNNER_WRITE_CONNECTIONS", err)
        self.assertEqual(self.executed(), [])

    def test_prod_words(self):
        for name, prod in (("PROD_db", True), ("product_service_view", False), ("DR_billing_2026", True),
                           ("db_core_standby_read_only", True), ("UAT_orders", False), ("live-orders", True)):
            self.assertEqual(sql_runner.is_prod(name), prod, name)

    def test_helpers_only_for_mysql(self):
        code, _, err = self.run_cli("tables", "s", "--conn", "core_ora")
        self.assertEqual(code, 2)
        self.assertIn("oracle", err)
        code, _, _ = self.run_cli("query", "select 1 from dual", "--conn", "core_ora")
        self.assertEqual(code, 0)

    def test_connection_name_with_spaces(self):
        code, _, _ = self.run_cli("query", "select 1", "--conn", "product_service_view")
        self.assertEqual(code, 0)
        self.assertEqual(self.executed()[0]["database"]["id"], "11")

    def test_describe_and_tables_validate_names(self):
        code, out, _ = self.run_cli("describe", "s.t", "--conn", "UAT_db")
        self.assertEqual(code, 0)
        self.assertIn("CREATE TABLE `t` (...)", out)
        self.assertEqual(self.executed()[0]["query"], "show create table `s`.`t`")
        code, _, _ = self.run_cli("tables", "x' or '1'='1", "--conn", "UAT_db")
        self.assertEqual(code, 2)

    def test_login_failure_and_config(self):
        os.environ["SQL_RUNNER_PASSWORD"] = "wrong"
        code, _, err = self.run_cli("query", "select 1", "--conn", "UAT_db")
        self.assertEqual(code, 6)
        del os.environ["SQL_RUNNER_PASSWORD"]
        os.environ["VMS_SQL_RUNNER_PASSWORD"] = PASSWORD
        code, _, _ = self.run_cli("query", "select 1", "--conn", "UAT_db")
        self.assertEqual(code, 0)
        del os.environ["SQL_RUNNER_URL"]
        code, _, err = self.run_cli("doctor")
        self.assertEqual(code, 3)

    def test_network_error(self):
        os.environ["SQL_RUNNER_URL"] = "http://127.0.0.1:1"
        code, _, err = self.run_cli("query", "select 1", "--conn", "UAT_db")
        self.assertEqual(code, 5)


if __name__ == "__main__":
    unittest.main()
