#!/usr/bin/env python3
"""扫描 cards/ 下所有 session.db，用当前评分代码做全库质量分布审计。

只读数据库，不修改任何卡片。输出：
- 控制台摘要（总量、分布、阈值命中、子维度分布）
- outputs/quality_audit_all_sessions.json（逐会话 + 全量明细）
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sqlite3
import statistics as st
import struct
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import sqlite_vec
except ImportError:
    sqlite_vec = None

from backend.models.card import Card
from backend.quality.scorer import score_all_cards


def _parse_json(text, fallback):
    try:
        return json.loads(text or fallback)
    except Exception:
        return fallback


def load_session_db(path: str):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cards: list[Card] = []
    try:
        for row in con.execute("SELECT * FROM cards"):
            d = dict(row)
            d["links"] = _parse_json(d.get("links"), [])
            d["backlinks"] = _parse_json(d.get("backlinks"), [])
            d["sources"] = _parse_json(d.get("sources"), [])
            d["tags"] = _parse_json(d.get("tags"), [])
            d["metadata"] = _parse_json(d.get("metadata"), {})
            try:
                cards.append(Card(**d))
            except Exception:
                continue

        embeddings = {}
        try:
            if sqlite_vec is not None:
                con.enable_load_extension(True)
                sqlite_vec.load(con)
                con.enable_load_extension(False)
            mp = {cid: rid for cid, rid in con.execute("SELECT card_id, rowid FROM card_vec_map")}
            ids = [c.id for c in cards if c.id in mp]
            if len(ids) >= 2:
                for cid in ids:
                    row = con.execute("SELECT rowid, * FROM cards_vec WHERE rowid=?", (mp[cid],)).fetchone()
                    if row is not None and isinstance(row[2], bytes):
                        embeddings[cid] = list(struct.unpack("<512f", row[2]))
        except Exception:
            embeddings = {}
        return cards, embeddings
    finally:
        con.close()


def percentile(values, p):
    vals = sorted(values)
    if not vals:
        return None
    idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * p))))
    return vals[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="outputs/quality_audit_all_sessions.json")
    ap.add_argument("--min-cards", type=int, default=2)
    args = ap.parse_args()

    session_rows = []
    all_scores = []
    for path in sorted(glob.glob("cards/*/*/session.db")):
        parts = Path(path).parts
        if len(parts) < 3:
            continue
        username, session_id = parts[-3], parts[-2]
        cards, embeddings = load_session_db(path)
        if len(cards) < args.min_cards:
            continue
        scores = score_all_cards(cards, embeddings or None)
        gaps = [s["gap_score"] for s in scores]
        session_rows.append({
            "path": path,
            "username": username,
            "session_id": session_id,
            "n_cards": len(cards),
            "n_embeddings": len(embeddings),
            "gap_min": min(gaps),
            "gap_max": max(gaps),
            "gap_mean": st.mean(gaps),
            "gap_sd": st.pstdev(gaps),
            "n_low": sum(g < 0.3 for g in gaps),
            "n_mid": sum(0.3 <= g < 0.7 for g in gaps),
            "n_high": sum(g >= 0.7 for g in gaps),
            "scores": scores,
        })
        all_scores.extend(scores)

    n = len(all_scores)
    gaps = [s["gap_score"] for s in all_scores]
    report = {
        "n_sessions": len(session_rows),
        "n_cards": n,
        "gap_min": min(gaps),
        "gap_p01": percentile(gaps, 0.01),
        "gap_p05": percentile(gaps, 0.05),
        "gap_p10": percentile(gaps, 0.10),
        "gap_p25": percentile(gaps, 0.25),
        "gap_p50": percentile(gaps, 0.50),
        "gap_mean": st.mean(gaps),
        "gap_p75": percentile(gaps, 0.75),
        "gap_p90": percentile(gaps, 0.90),
        "gap_p95": percentile(gaps, 0.95),
        "gap_p99": percentile(gaps, 0.99),
        "gap_max": max(gaps),
        "gap_sd": st.pstdev(gaps),
        "n_low_lt_0.3": sum(g < 0.3 for g in gaps),
        "n_mid_0.3_0.7": sum(0.3 <= g < 0.7 for g in gaps),
        "n_high_ge_0.7": sum(g >= 0.7 for g in gaps),
        "histogram_0.1": {
            f"[{i / 10:.1f}, {(i + 1) / 10:.1f})": sum(i / 10 <= g < (i + 1) / 10 for g in gaps)
            for i in range(10)
        },
        "subscores": {},
        "sessions": session_rows,
    }
    for key in ["structure_score", "graph_score", "semantic_score", "confidence_score"]:
        vals = [s[key] for s in all_scores]
        report["subscores"][key] = {
            "min": min(vals), "max": max(vals), "mean": st.mean(vals), "sd": st.pstdev(vals),
        }
    for key in ["content", "sources", "links"]:
        vals = [s["dimensions"][key] for s in all_scores]
        report["subscores"][key] = {
            "min": min(vals), "max": max(vals), "mean": st.mean(vals), "sd": st.pstdev(vals),
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"sessions={report['n_sessions']} cards={report['n_cards']}")
    print(f"gap min={report['gap_min']:.4f} p10={report['gap_p10']:.4f} "
          f"p50={report['gap_p50']:.4f} p90={report['gap_p90']:.4f} max={report['gap_max']:.4f} "
          f"mean={report['gap_mean']:.4f} sd={report['gap_sd']:.4f}")
    print(f"low(<0.3)={report['n_low_lt_0.3']} mid(0.3~0.7)={report['n_mid_0.3_0.7']} "
          f"high(>=0.7)={report['n_high_ge_0.7']}")
    print("hist:", report["histogram_0.1"])
    print("subscores:", json.dumps(report["subscores"], ensure_ascii=False))
    print(f"written: {out}")


if __name__ == "__main__":
    main()
