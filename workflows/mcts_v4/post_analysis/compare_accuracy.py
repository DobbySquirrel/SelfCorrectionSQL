#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import re
from typing import Any, Dict, List, Tuple

def _fix_common_json_issues(s: str) -> str:
    """
    针对常见脏数据做“保守修复”：
    1) 去掉 `,,` 这类多余逗号
    2) 去掉 `,}` / `,]` 这种尾逗号
    3) 轻微清理不可见字符
    """
    s = s.replace("\ufeff", "").strip()

    # 反复把 , , 合并成 ,
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r",\s*,", ",", s)

    # 删除尾逗号： ,} 或 ,]
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return s

def _try_load_json(text: str) -> Any:
    return json.loads(text)

def _try_load_jsonlines(text: str) -> List[Any]:
    objs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        objs.append(json.loads(line))
    return objs

def _try_load_multi_objects(text: str) -> List[Any]:
    """
    解析“多个 JSON 对象被直接拼接”的情况：
    例如：{...}{...}{...} 或 {...}\n{...}
    使用 raw_decode 逐段解析。
    """
    dec = json.JSONDecoder()
    i, n = 0, len(text)
    objs = []
    while i < n:
        # 跳过空白
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        obj, j = dec.raw_decode(text, i)
        objs.append(obj)
        i = j
    return objs

def load_any_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    # 先按原样尝试
    try:
        return _try_load_json(raw)
    except Exception:
        pass

    # 修复后再尝试
    fixed = _fix_common_json_issues(raw)
    try:
        return _try_load_json(fixed)
    except Exception:
        pass

    # JSON Lines 兜底
    try:
        return _try_load_jsonlines(fixed)
    except Exception:
        pass

    # 多对象拼接兜底
    try:
        return _try_load_multi_objects(fixed)
    except Exception as e:
        raise RuntimeError(
            f"无法解析为 JSON / JSONL / 多对象拼接 JSON：{path}\n"
            f"你可以先 head -n 50 看一下文件格式，或把开头 200 行贴出来。\n"
            f"原始错误：{e}"
        )

def extract_error_details(obj: Any) -> List[Dict[str, Any]]:
    """
    从任意层级结构中提取所有 error_details（列表）。
    文件可能是 dict 或 list，甚至 list[dict]。
    """
    out: List[Dict[str, Any]] = []

    def walk(x: Any):
        if isinstance(x, dict):
            if "error_details" in x and isinstance(x["error_details"], list):
                for item in x["error_details"]:
                    if isinstance(item, dict):
                        out.append(item)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(obj)
    return out

def build_map(details: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    用 key = question_id::idx 做索引（尽量避免重复）。
    保存 success 与 question 文本等信息。
    """
    mp: Dict[str, Dict[str, Any]] = {}
    for d in details:
        qid = d.get("question_id", "")
        idx = d.get("idx", "")
        key = f"{qid}::{idx}"
        # 有些日志可能重复记录同一个 key：优先保留最后一次（你也可改成第一次）
        mp[key] = {
            "question_id": qid,
            "idx": idx,
            "success": int(d.get("success", 0)) if str(d.get("success", "0")).isdigit() else d.get("success"),
            "question": d.get("question", ""),
            "difficulty": d.get("difficulty", ""),
            "db_id": d.get("db_id", ""),
            "db_path": d.get("db_path", ""),
            "evidence": d.get("evidence", ""),
        }
    return mp

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--good", required=True, help="对的那份 JSON（success=1 代表对）")
    ap.add_argument("--bad", required=True, help="错的那份 JSON（success=0/!=1 代表错）")
    ap.add_argument("--out", default="", help="可选：输出 JSON 文件路径")
    args = ap.parse_args()

    good_obj = load_any_json(args.good)
    bad_obj = load_any_json(args.bad)

    good_details = extract_error_details(good_obj)
    bad_details = extract_error_details(bad_obj)

    good_map = build_map(good_details)
    bad_map = build_map(bad_details)

    diffs = []
    missing_in_bad = []

    for key, g in good_map.items():
        g_succ = g.get("success", 0)
        b = bad_map.get(key)
        if b is None:
            if g_succ == 1:
                missing_in_bad.append(g)
            continue
        b_succ = b.get("success", 0)

        # 你要的：good 对，但 bad 错
        if g_succ == 1 and b_succ != 1:
            diffs.append({
                "key": key,
                "good_success": g_succ,
                "bad_success": b_succ,
                "question": g.get("question", ""),
                "question_id": g.get("question_id", ""),
                "idx": g.get("idx", ""),
                "difficulty": g.get("difficulty", ""),
                "db_id": g.get("db_id", ""),
            })

    print(f"[结果] good 对但 bad 错：{len(diffs)} 条")
    for i, d in enumerate(diffs[:200], 1):  # 默认最多打印 200 条，避免刷屏
        print(f"\n#{i}  key={d['key']}  (good={d['good_success']} bad={d['bad_success']})")
        print(f"Q: {d['question']}")

    if missing_in_bad:
        print(f"\n[补充] good 里 success=1 但 bad 里找不到对应 key：{len(missing_in_bad)} 条")

    if args.out:
        payload = {
            "good_path": args.good,
            "bad_path": args.bad,
            "count_good_correct_bad_wrong": len(diffs),
            "items": diffs,
            "count_good_correct_missing_in_bad": len(missing_in_bad),
            "missing_in_bad": missing_in_bad,
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n[已写入] {args.out}")

if __name__ == "__main__":
    main()

python3 workflows/mcts_v1/post_analysis/compare_accuracy.py \
  --good /hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL/workflows/mcts_v1/test/out/12_14_closenarrow_stats_acc.json \
  --bad  /hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL/workflows/mcts_v1/test/out/error_analysis_1_7_test_no_strategy_sql_acc.json \
  --out  /hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL/workflows/mcts_v1/test/out/good_correct_bad_wrong.json

