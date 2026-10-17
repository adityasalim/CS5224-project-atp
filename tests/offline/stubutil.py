"""Helpers for the fake Bedrock converse() in the agent tests."""
CACHE_SEEN = []   # (role line, number of cachePoint blocks) per call


def sys_text(k):
    """All system text blocks, role instructions first (the cached trip+pool context comes before them)."""
    texts = [b["text"] for b in k["system"] if "text" in b]
    n = sum(1 for b in k["system"] if "cachePoint" in b) + sum(
        1 for t in (k.get("toolConfig") or {}).get("tools", []) if "cachePoint" in t) + sum(
        1 for m in k["messages"] for c in m["content"] if "cachePoint" in c)
    texts.sort(key=lambda t: not t.startswith("ROLE:"))
    CACHE_SEEN.append((texts[0].split("\n")[0][:50], n))
    return "\n".join(texts)


def clean_msgs(k):
    return [{**m, "content": [c for c in m["content"] if "cachePoint" not in c]} for m in k["messages"]]
