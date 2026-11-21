"""Render a printable, read-only view of a case and its evidence."""

import json
from html import escape
from zoneinfo import ZoneInfo

from app import rule_summary

STATUSES = {"OPEN": "待處理", "IN_PROGRESS": "調查中", "CLOSED": "已結案"}
TAIPEI = ZoneInfo("Asia/Taipei")


def safe(value):
    return escape(str(value))


def timestamp(value):
    return value.astimezone(TAIPEI).isoformat(timespec="seconds") if value else "—"


def statements(items):
    return "".join(
        f"<li>{safe(item.text)} <span class='refs'>{safe(' / '.join(item.evidence_ids))}</span></li>"
        for item in items
    )


def render_report(case: dict, run: dict, hashes: dict[str, str]) -> str:
    fields = [
        ("案件編號", case["case_id"]),
        ("訂單", case["order_id"]),
        ("付款", case["payment_id"]),
        ("處理狀態", STATUSES[case["status"]]),
        ("案件版本", case["version"]),
        ("比對批次", case["run_id"]),
        ("比對規則", case["rule"]),
        ("比對規則版本", run["rules_version"]),
        ("比對基準時間", timestamp(run["as_of"])),
        ("最後更新", timestamp(case["updated_at"])),
        ("結案時間", timestamp(case["closed_at"])),
    ]
    metadata = "".join(f"<div><dt>{safe(label)}</dt><dd>{safe(value)}</dd></div>" for label, value in fields)
    evidence = []
    for index, item in enumerate(case["evidence"], start=1):
        location = f"第 {item['line']} 列" if "line" in item else "查無紀錄的條件與涵蓋範圍"
        record = item.get("record", item)
        raw = json.dumps(record, ensure_ascii=False, indent=2)
        digest = hashes.get(item["file"])
        fingerprint = f"<p class='muted fingerprint'>SHA-256：{safe(digest)}</p>" if digest else ""
        evidence.append(
            f"<section class='evidence'><h3>E{index} · {safe(item['file'])} · {safe(location)}</h3>"
            f"{fingerprint}<pre>{safe(raw)}</pre></section>"
        )

    history = "".join(
        f"<li>{safe(timestamp(event['created_at']))} · {safe(event.get('actor_username') or '未記錄操作者')} · "
        f"{safe(STATUSES[event['from_status']])} → {safe(STATUSES[event['to_status']])}"
        f"<div class='note'>{safe(event['note'])}</div></li>"
        for event in case["history"]
    )
    history_section = f"<ul>{history}</ul>" if history else "<p>尚無處理紀錄。</p>"

    try:
        content, _ = rule_summary.build_summary(case)
    except rule_summary.RuleSummaryError:
        findings = "<p>目前證據無法產生規則式摘要，請人工核對下方原始資料。</p>"
    else:
        missing = "".join(f"<li>{safe(item)}</li>" for item in content.missing_information)
        findings = (
            f"<p class='overview'>{safe(content.overview)}</p>"
            f"<h3>證據重點</h3><ul>{statements(content.confirmed_facts)}</ul>"
            f"<h3>建議查核步驟</h3><ol>{statements(content.recommended_steps)}</ol>"
            f"<h3>尚缺資料</h3><ul>{missing}</ul>"
            f"<p class='muted'>摘要規則版本 {safe(rule_summary.RULE_VERSION)}</p>"
        )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>查核報告-{safe(case['order_id'])}</title>
  <link rel="stylesheet" href="/static/report.css">
</head>
<body>
  <main>
    <header>
      <h1>單一案件查核報告</h1>
      <button type="button" onclick="window.print()">列印／另存 PDF</button>
    </header>
    <dl>{metadata}</dl>
    <h2>異常概述與查核步驟</h2>
    <p>{safe(case["summary"])}</p>
    {findings}
    <h2>原始證據</h2>
    {"".join(evidence)}
    <h2>處理紀錄</h2>
    {history_section}
  </main>
</body>
</html>"""
