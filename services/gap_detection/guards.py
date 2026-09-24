"""Guards that keep gap analysis on-topic and safe."""
import json
import re
from sqlalchemy import text

MIN_WORDS = 60

BLOCKED = ["suicid", "self-harm", "self harm", "selfharm", "kill myself", "kill yourself", "porn", "overdose", "escort"]

def _pattern(words):
    return "(" + "|".join(re.escape(w.lower()) for w in words) + ")"

def blocked_pattern():
    return _pattern(BLOCKED)

def allow_pattern(keywords):
    return _pattern(keywords)

def is_blocked(topic):
    t = (topic or "").lower()
    return any(b in t for b in BLOCKED)

async def load_niche_keywords(session, tenant_id):
    row = (await session.execute(text("SELECT metadata->'niche_keywords' AS k FROM tenants WHERE id = :t"), {"t": tenant_id})).first()
    k = row.k if row and row.k else []
    if isinstance(k, str):
        k = json.loads(k)
    return [x.lower() for x in k if isinstance(x, str) and x.strip()]

async def document_has_enough_content(session, document_id, tenant_id, min_words=MIN_WORDS):
    row = (await session.execute(text("SELECT raw_text FROM documents WHERE id = :d AND tenant_id = :t"), {"d": document_id, "t": tenant_id})).first()
    return bool(row and row.raw_text and len(row.raw_text.split()) >= min_words)
