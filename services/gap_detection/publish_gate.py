import re

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF]")
SYMBOLS = re.compile(r"[*#_~`|<>{}\[\]\\^%@]|\u2014|\u2013|\u2022|\u2192|\u00a9|\u00ae|\u2122")
PLACEHOLDER = re.compile(r"TODO|TBD|lorem ipsum|NEEDS_INPUT", re.I)
BANNED = ["delve", "game-changer", "game changer", "seamless", "unlock", "in today's fast-paced",
          "landscape", "revolutionize", "cutting-edge", "studies show", "research shows",
          "experts say", "according to a survey", "as an ai"]


def check(text, keyword, min_words=600):
    """Return a list of reasons the draft must not be published. Empty list means it passes."""
    reasons = []
    t = text or ""
    if EMOJI.search(t):
        reasons.append("emoji")
    if SYMBOLS.search(t):
        reasons.append("symbol or dash or bracket")
    if " - " in t:
        reasons.append("spaced hyphen used as dash")
    if PLACEHOLDER.search(t):
        reasons.append("placeholder text")
    low = t.lower()
    for b in BANNED:
        if b in low:
            reasons.append("banned phrase: " + b)
    if len(t.split()) < min_words:
        reasons.append("too short")
    if keyword.lower() not in low:
        reasons.append("keyword missing")
    return reasons
