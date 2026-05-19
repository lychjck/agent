from typing import Any


def merge_unique_strings(left: Any, right: Any) -> list[str]:
    output: list[str] = []
    for value in list(left or []) + list(right or []):
        text = str(value).strip()
        if text and text not in output:
            output.append(text)
    return output


def normalize_report_payload(report_payload: dict[str, Any] | None) -> dict[str, Any]:
    report = dict(report_payload or {})
    if isinstance(report.get("report"), dict):
        report = dict(report["report"])
    return report


def merge_final_report_patch(base_payload: dict[str, Any] | None, patch_payload: dict[str, Any] | None) -> dict[str, Any]:
    base = normalize_report_payload(base_payload)
    patch = normalize_report_payload(patch_payload)
    merged = dict(base)
    for key, value in patch.items():
        if key in {"holding_analysis", "limitations", "evidence"}:
            continue
        if value not in (None, "", [], {}):
            merged[key] = value
    base_items = [
        item for item in base.get("holding_analysis", [])
        if isinstance(item, dict)
    ] if isinstance(base.get("holding_analysis"), list) else []
    patch_items = [
        item for item in patch.get("holding_analysis", [])
        if isinstance(item, dict)
    ] if isinstance(patch.get("holding_analysis"), list) else []
    by_code: dict[str, dict[str, Any]] = {}
    ordered_codes: list[str] = []
    for item in base_items + patch_items:
        code = str(item.get("target_code") or item.get("code") or "").strip()
        key = code or f"__index_{len(ordered_codes)}"
        if key not in ordered_codes:
            ordered_codes.append(key)
        by_code[key] = item
    if ordered_codes:
        merged["holding_analysis"] = [by_code[key] for key in ordered_codes]
    merged["limitations"] = merge_unique_strings(base.get("limitations"), patch.get("limitations"))
    merged["evidence"] = merge_unique_strings(base.get("evidence"), patch.get("evidence"))
    return merged
