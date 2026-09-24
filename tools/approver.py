import argparse, json, subprocess, sys
sys.path.insert(0, ".")
from services.gap_detection.publish_gate import check

PSQL = ["docker", "exec", "-i", "data_engine_postgres", "psql",
        "-U", "admin", "-d", "data_engine", "-t", "-A"]


def db(query, **variables):
    cmd = PSQL[:]
    for k, v in variables.items():
        cmd += ["-v", k + "=" + str(v)]
    r = subprocess.run(cmd, input=query, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("database error: " + r.stderr.strip())
    return r.stdout.strip()

ap = argparse.ArgumentParser()
ap.add_argument("--keyword", required=True)
ap.add_argument("--min-words", type=int, default=600)
ap.add_argument("--spacing-hours", type=int, default=48)
a = ap.parse_args()

LIST = """SELECT COALESCE(json_agg(json_build_object(
'id', id, 'text', draft_text)), '[]')
FROM gap_content_drafts WHERE status = 'embedded';"""

REJECT = """UPDATE gap_content_drafts SET status = 'rejected',
gate_report = :'rep', updated_at = NOW() WHERE id = :'id'::uuid;"""

APPROVE = """UPDATE gap_content_drafts SET status = 'approved',
gate_report = 'passed', updated_at = NOW(),
publish_at = (SELECT GREATEST(NOW(), COALESCE(MAX(publish_at), NOW())
+ make_interval(hours => :hrs)) FROM gap_content_drafts
WHERE status = 'approved') WHERE id = :'id'::uuid;"""

rows = json.loads(db(LIST))
print(len(rows), "embedded draft(s) to review")
for r in rows:
    reasons = check(r["text"], a.keyword, a.min_words)
    if reasons:
        db(REJECT, rep="; ".join(reasons), id=r["id"])
        print("REJECTED", r["id"], reasons)
    else:
        db(APPROVE, hrs=a.spacing_hours, id=r["id"])
        print("APPROVED", r["id"])
