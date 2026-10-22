"""AI output is advice only (Gamma G5).

Every path that stores or shows a model's opinion of a listing goes through
advisory_result: an allowlist of advisory text, a verdict of REVIEW or SKIP,
and no bid, valuation or readiness field from the model.
"""
import copy

NOTICE = "AI research advice only; evidence-backed diligence is still required."


def advisory_result(value):
    """Allowlist advisory content; drop all model financial/action authorisation fields."""
    if not isinstance(value, dict):
        return {"error": "Invalid AI response", "advisory_only": True,
                "acquisition_ready": False}
    allowed = ("summary", "positives", "risks", "red_flags", "next_steps")
    out = {}
    for key in allowed:
        item = value.get(key)
        if key == "summary" and isinstance(item, str):
            out[key] = item
        elif key != "summary" and isinstance(item, list) and all(isinstance(x, str) for x in item):
            out[key] = copy.deepcopy(item)
    verdict = value.get("verdict")
    out["verdict"] = "SKIP" if verdict == "SKIP" else "REVIEW"
    out.update(advisory_only=True, acquisition_ready=False,
               bid_suggestions_available=False, notice=NOTICE)
    return out
