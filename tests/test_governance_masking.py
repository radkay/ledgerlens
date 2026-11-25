import re
import sqlite3
import unittest

from provisioning import provision


class IdentifierMaskTests(unittest.TestCase):
    def test_both_silver_values_and_workbench_projection(self):
        for env, other_env in (("dev", "demo"), ("demo", "dev")):
            with self.subTest(environment=env):
                self.assert_masking_policy(env, other_env)

    def assert_masking_policy(self, env, other_env):
        catalog = f"ledgerlens_{env}"
        statements = list(provision.ddl(catalog))
        mask_functions = (
            ("is_identifier_field", ("field_name",)),
            ("mask_workbench_identifier", ("value", "field_name")),
            ("mask_identifier", ("value", "field_name")),
        )
        member_of = set()
        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)
        database.create_function("is_account_group_member", 1, member_of.__contains__)
        database.create_function("concat", 2, lambda prefix, tail: prefix + tail)
        database.create_function(
            "regexp_like", 2,
            lambda value, pattern: re.search(pattern.replace(r"\z", r"\Z"), value) is not None
            if value is not None else None,
        )

        for name, parameters in mask_functions:
            statement, = [
                sql for sql in statements
                if sql.startswith(f"CREATE OR REPLACE FUNCTION {catalog}.silver.{name}(")
            ]
            body = statement.split("\nRETURN ", 1)[1]
            self.register_sql_function(database, catalog, name, parameters, body)

        for column in ("extracted_value", "final_value"):
            self.assertIn(
                f"ALTER TABLE {catalog}.silver.fields\n"
                f"  ALTER COLUMN {column} SET MASK {catalog}.silver.mask_identifier USING COLUMNS (field_name)",
                statements,
            )

        workbench, = [sql for sql in statements if sql.startswith(f"ALTER VIEW {catalog}.review.current_workbench")]
        projections = re.findall(
            rf"({catalog}\.silver\.mask_workbench_identifier\(f\.(?:extracted_value|final_value), f\.field_name\)"
            r" AS (?:extracted_value|final_value))",
            workbench,
        )
        self.assertEqual(len(projections), 2)
        workbench_query = self.local_sql(catalog, "SELECT " + ", ".join(projections)) + (
            " FROM (SELECT ? AS extracted_value, ? AS final_value, ? AS field_name) AS f"
        )

        reviewer = f"ledgerlens-{env}-reviewers"
        governed = f"ledgerlens-{env}-identifier-readers"
        raw_values = ("000-00-0123", "000000987")
        last_four = ("****0123", "****0987")
        for memberships, expected in (
            ((), (None, None)),
            ((f"ledgerlens-{other_env}-reviewers", f"ledgerlens-{other_env}-identifier-readers"), (None, None)),
            ((reviewer,), last_four),
            ((governed,), raw_values),
            ((reviewer, governed), raw_values),
        ):
            with self.subTest(memberships=memberships):
                member_of.clear()
                member_of.update(memberships)
                masked = database.execute(
                    "SELECT mask_identifier(?, 'employee_ssn'), mask_identifier(?, 'employee_ssn')",
                    raw_values,
                ).fetchone()
                self.assertEqual(masked, expected)
                self.assertEqual(
                    database.execute(workbench_query, (*masked, "employee_ssn")).fetchone(),
                    last_four if expected[0] is not None else (None, None),
                )
                self.assertEqual(database.execute("SELECT mask_identifier('123.45', 'wages')").fetchone(),
                                 ("123.45",))

        member_of.clear()
        member_of.add(reviewer)
        for value, name, expected in (
            ("00-0004321", "employer_ein", "****4321"),
            ("000 00 0001", "social_security_number", "****0001"),
            ("000-00-0123\n", "ssn", None),
            ("id 000-00-0123", "TIN", None),
            ("0123", "employee_ssn", None),
            (None, "employee_ssn", None),
        ):
            with self.subTest(field=name):
                self.assertEqual(database.execute("SELECT mask_identifier(?, ?)", (value, name)).fetchone(),
                                 (expected,))

    @staticmethod
    def local_sql(catalog, statement):
        return statement.replace(f"{catalog}.silver.", "").replace("r'", "'")

    def register_sql_function(self, database, catalog, name, parameters, body):
        columns = ", ".join(f"? AS {param}" for param in parameters)
        statement = self.local_sql(catalog, f"SELECT {body} FROM (SELECT {columns})")

        def evaluate(*args):
            return database.execute(statement, args).fetchone()[0]

        database.create_function(name, len(parameters), evaluate)
