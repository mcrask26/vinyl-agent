"""Optional (paid): Claude rewrites recommendation reasons in friendlier words.

Off unless ANTHROPIC_API_KEY is set. Any problem (no key, error, refusal, odd answer) returns
None, and the app keeps the free rule-based reasons. Claude never changes facts or prices:
it only gets the reason text and must return one sentence per item.
"""

import json

from core.config import get_anthropic_key, get_claude_model

SYSTEM = ("You rewrite short record-recommendation reasons for a vinyl collector. Keep every fact (artist names, "
          "album names, years, genres) exactly as given and add no new facts. One friendly sentence each, under 30 words. "
          'Reply with only a JSON array of strings, same order and length as the input.')


def enabled() -> bool:
    return bool(get_anthropic_key())


def rewrite_reasons(reasons: list):
    """New wording for each reason, or None to keep the originals."""
    if not reasons or not enabled():
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=get_anthropic_key(), max_retries=2, timeout=60)
        resp = client.messages.create(model=get_claude_model(), max_tokens=4000, system=SYSTEM,
                                      messages=[{"role": "user", "content": json.dumps(reasons)}])
        if resp.stop_reason not in ("end_turn", "stop_sequence"):
            return None
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        text = text[text.find("["): text.rfind("]") + 1]
        out = json.loads(text)
    except Exception:
        return None
    if not isinstance(out, list) or len(out) != len(reasons) or not all(isinstance(s, str) and s for s in out):
        return None
    return out
