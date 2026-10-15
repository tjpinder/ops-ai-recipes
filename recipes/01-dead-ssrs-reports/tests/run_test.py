"""Test the dead-report query against a synthetic ReportServer.

Loads the fixture (a fictional distributor's SSRS estate from the sql-xray
demo) into a throwaway database on a local SQL Server, runs
find_dead_reports.sql pinned to the fixture's window, and compares the
result with the fixture's answer key. Drops the database afterwards.

Usage:  python run_test.py [--server localhost] [--keep]
Needs:  sqlcmd on PATH, Windows auth to a SQL Server you can create a database on.
"""
import argparse
import base64
import csv
import io
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
FIX = HERE / "fixture"
QUERY = HERE.parent / "find_dead_reports.sql"
DB = "ReportServerRecipeTest"


def q(v):
    """SQL literal for a fixture value (synthetic data; quotes doubled)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    return "N'" + str(v).replace("'", "''") + "'"


def inserts(table, cols, rows, convert=None):
    out = []
    for i in range(0, len(rows), 500):
        vals = []
        for r in rows[i:i + 500]:
            vals.append("(" + ", ".join((convert or {}).get(c, q)(r.get(c)) for c in cols) + ")")
        out.append(f"INSERT INTO dbo.{table} ({', '.join(cols)}) VALUES\n" + ",\n".join(vals) + ";")
    return "\n".join(out)


def content_literal(b64):
    return "NULL" if not b64 else "0x" + base64.b64decode(b64).hex()


def load_script():
    load = lambda name: json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))
    parts = [
        f"IF DB_ID('{DB}') IS NOT NULL BEGIN ALTER DATABASE {DB} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE {DB}; END;",
        f"CREATE DATABASE {DB};",
        "GO",
        f"USE {DB};",
        """CREATE TABLE dbo.Catalog (ItemID uniqueidentifier PRIMARY KEY, Path nvarchar(425), Name nvarchar(425),
             Type int, Content image NULL, Hidden bit, LinkSourceID uniqueidentifier NULL,
             CreationDate datetime, ModificationDate datetime);
           CREATE TABLE dbo.ExecutionLog3 (ItemPath nvarchar(425), UserName nvarchar(260), RequestType nvarchar(30),
             Format nvarchar(26), ItemAction nvarchar(30), Source nvarchar(30), TimeStart datetime, Status nvarchar(40));
           CREATE TABLE dbo.Subscriptions (SubscriptionID uniqueidentifier, Report_OID uniqueidentifier,
             LastStatus nvarchar(260), LastRunTime datetime, EventType nvarchar(260), InactiveFlags int);
           CREATE TABLE dbo.ConfigurationInfo (Name nvarchar(260), Value ntext);""",
        inserts("Catalog", ["ItemID", "Path", "Name", "Type", "Content", "Hidden", "LinkSourceID", "CreationDate", "ModificationDate"],
                load("Catalog"), {"Content": content_literal}),
        inserts("ExecutionLog3", ["ItemPath", "UserName", "RequestType", "Format", "ItemAction", "Source", "TimeStart", "Status"],
                load("ExecutionLog3")),
        inserts("Subscriptions", ["SubscriptionID", "Report_OID", "LastStatus", "LastRunTime", "EventType", "InactiveFlags"],
                load("Subscriptions")),
        inserts("ConfigurationInfo", ["Name", "Value"], load("ConfigurationInfo")),
    ]
    return "\n".join(parts) + "\nGO\n"


def sqlcmd(server, args):
    r = subprocess.run(["sqlcmd", "-S", server, "-E", "-C", "-b", *args], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"sqlcmd failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="localhost")
    ap.add_argument("--keep", action="store_true", help="leave the test database in place")
    a = ap.parse_args()

    expected = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
    as_of = expected["usage_window"]["end"]

    with tempfile.TemporaryDirectory() as tmp:
        load = pathlib.Path(tmp) / "load.sql"
        load.write_text(load_script(), encoding="utf-8")
        sqlcmd(a.server, ["-i", str(load)])

        query = QUERY.read_text(encoding="utf-8").replace(
            "DECLARE @AsOf       datetime = GETDATE();", f"DECLARE @AsOf       datetime = '{as_of}';")
        assert as_of in query, "could not pin @AsOf"
        qf = pathlib.Path(tmp) / "query.sql"
        qf.write_text(query, encoding="utf-8")
        out = sqlcmd(a.server, ["-d", DB, "-i", str(qf), "-s", "\t", "-W", "-h", "-1"])

        # Guardrail: a window longer than the log covers must be refused, not answered.
        long_q = pathlib.Path(tmp) / "query_90.sql"
        long_q.write_text(query.replace("DECLARE @WindowDays int      = NULL;", "DECLARE @WindowDays int      = 90;"),
                          encoding="utf-8")
        r = subprocess.run(["sqlcmd", "-S", a.server, "-E", "-C", "-b", "-d", DB, "-i", str(long_q)],
                           capture_output=True, text=True)
        refused = r.returncode != 0 and "Refusing to judge" in (r.stdout + r.stderr)

    if not a.keep:
        sqlcmd(a.server, ["-Q", f"ALTER DATABASE {DB} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE {DB};"])

    got = {row[1]: row[0] for row in csv.reader(io.StringIO(out), delimiter="\t") if len(row) > 1}

    # The answer key uses sql-xray's labels. Map them to this recipe's statuses.
    want_map = {
        "no runs in window": {"retire candidate", "review"},
        "not logged": {"not logged (subreport)", "not logged (linked)"},
        "active": {"active"},
        "low": {"active"},  # low use is still use: never a retire candidate here
    }
    failures = []
    for path, label in expected["report_usage"].items():
        if got.get(path) not in want_map[label]:
            failures.append(f"{path}: expected {label}, got {got.get(path)}")
    extra = sorted(set(got) - set(expected["report_usage"]))
    # The answer key's R06 risk: no runs in the window, but an active subscription sends it.
    if got.get("/Finance/Quarterly Close Pack") != "review":
        failures.append(f"/Finance/Quarterly Close Pack: expected review (R06), got {got.get('/Finance/Quarterly Close Pack')}")
    if not refused:
        failures.append("a 90-day window over a 60-day log was answered instead of refused")
    else:
        print("Guardrail: 90-day window over a 60-day log was refused")

    counts = {}
    for s in got.values():
        counts[s] = counts.get(s, 0) + 1
    print("Statuses:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("Retire candidates:")
    for p, s in sorted(got.items()):
        if s == "retire candidate":
            print("  ", p)
    if extra:
        print("Reports not in the answer key:", extra)
    if failures:
        print("FAIL")
        print("\n".join(failures))
        sys.exit(1)
    print(f"PASS: {len(expected['report_usage'])} of {len(expected['report_usage'])} reports match the answer key")


if __name__ == "__main__":
    main()
