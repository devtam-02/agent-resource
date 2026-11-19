import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import db_write_guard as guard  # noqa: E402


def decide(command):
    result = guard.check(command)
    return result["hookSpecificOutput"]["permissionDecision"] if result else None


class GuardTest(unittest.TestCase):
    def test_sql_runner(self):
        R = "python3 /x/skills/sql-runner/scripts/sql_runner.py"
        self.assertIsNone(decide(f'{R} query "select 1" --conn UAT'))
        self.assertIsNone(decide(f'{R} query "update s.t set a=1" --conn UAT --write --affected-rows 3'))  # dry run
        result = guard.check(f'{R} query "update s.t set a=1 where id=2" --conn UAT_db --write --affected-rows 1 --confirm 3F9A1C')
        out = result["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn("UAT_db", out["permissionDecisionReason"])
        self.assertIn("update s.t set a=1 where id=2", out["permissionDecisionReason"])
        self.assertIn("số dòng 1", out["permissionDecisionReason"])
        self.assertEqual(decide(f"{R} query --file x.sql --conn P --write --allow-prod --confirm ABC123"), "ask")
        self.assertEqual(decide('python3 -c "import sql_runner; c.execute(1, \'delete from t\')"'), "deny")

    def test_direct_api_is_denied(self):
        self.assertEqual(decide("curl -s -X POST http://h/vms/api/wa/sql-runner/execute -d @b.json"), "deny")
        self.assertEqual(decide('python3 -c "import requests; requests.post(\'http://h/vms/api/auth/login\')"'), "deny")

    def test_database_clients(self):
        self.assertIsNone(decide('mysql -h db -e "select * from t limit 5"'))
        self.assertEqual(decide('mysql -h db -e "UPDATE t SET a=1"'), "ask")
        self.assertEqual(decide('echo "delete from t" | mariadb db'), "ask")
        self.assertEqual(decide("mysql db < migrate.sql"), "ask")
        self.assertEqual(decide("cat fix.sql | psql -d app"), "ask")
        self.assertEqual(decide('psql -d app -c "truncate t"'), "ask")
        self.assertEqual(decide("psql -d app -f fix.sql"), "ask")
        self.assertIsNone(decide('psql -d app -c "select count(*) from t"'))
        self.assertEqual(decide("mongosh app --eval 'db.users.deleteMany({})'"), "ask")
        self.assertIsNone(decide("mongosh app --eval 'db.users.find().limit(3)'"))
        self.assertEqual(decide("redis-cli -h r FLUSHALL"), "ask")
        self.assertIsNone(decide("redis-cli -h r get key"))
        self.assertIsNone(decide("mysqldump --no-data db > schema.sql"))
        self.assertIsNone(decide("git commit -m 'update mysql docs'"))
        self.assertIsNone(decide('grep -r "psql -c update" .'))
        self.assertEqual(decide('docker exec -it db mysql -e "delete from t"'), "ask")
        self.assertEqual(decide('kubectl exec -n x pod -- psql -c "drop table t"'), "ask")
        self.assertEqual(decide('sudo mysql -e "update t set a=1"'), "ask")
        self.assertEqual(decide("/usr/bin/mysql db < x.sql"), "ask")
        self.assertEqual(decide('cd /tmp && mysql -e "insert into t values(1)"'), "ask")

    def test_migrations(self):
        self.assertEqual(decide("mvn -q flyway:migrate -Dflyway.url=jdbc:x"), "ask")
        self.assertEqual(decide("flyway -url=jdbc:x migrate"), "ask")
        self.assertEqual(decide("./gradlew flywayMigrate"), "ask")
        self.assertEqual(decide("liquibase --changelog-file=c.yaml update"), "ask")
        self.assertIsNone(decide("flyway info"))
        self.assertIsNone(decide("mvn -q test"))

    def test_main_prints_hook_json(self):
        event = {"tool_name": "Bash", "tool_input": {"command": 'mysql -e "drop table t"'}}
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event))), mock.patch.object(sys, "stdout", out):
            self.assertEqual(guard.main(), 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "ask")
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("not json")), mock.patch.object(sys, "stdout", out):
            self.assertEqual(guard.main(), 0)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
