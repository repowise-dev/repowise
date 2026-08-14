for f in findings:
    if f.confidence >= SAFE_CONFIDENCE_THRESHOLD:
        summary["high"] += 1
    elif f.confidence >= RISK_CAP_CONFIDENCE:
        summary["medium"] += 1
    else:
        summary["low"] += 1