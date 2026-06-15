"""
MCTS 测试脚本（单样本/多样本测试）

使用标准的 MCTS 算法运行测试，支持：
- 单个样本测试（--qid）
- 多个样本测试（--qids）
- 全量测试（不指定 --qid/--qids）

从 --ppl_file 读取样本（与 test_mcts_workflow 的样本格式一致）。
"""

import json
import sys
import argparse
import time
import threading
import os
import random
import numpy as np
from pathlib import Path
from typing import List, Optional
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
from tqdm import tqdm
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # If python-dotenv is not installed, skip

from workflows.mcts_v4.mcts_workflow import MCTSWorkflow
from workflows.mcts_v4.core.database_connector import DatabaseConnector
from workflows.mcts_v4.utils.model_utils import get_llm_config, pick_model
import logging
logging.getLogger("autogen.oai.client").setLevel(logging.ERROR)


def build_db_connector(db_name: str) -> DatabaseConnector:
    """构建数据库连接器（使用环境变量或相对路径，不再硬编码绝对路径）"""
    # 直接传入数据库名称，DatabaseConnector 会自动从环境变量或相对路径查找
    db_connector = DatabaseConnector(db_name)
    if not db_connector.connect():
        raise RuntimeError(f"数据库连接失败: {db_connector.db_path}")
    return db_connector


def run_once(sample: dict, parallel_workers: int = 5, multi_base_urls: List[str] = None,
             mcts_config: dict = None, strategy_mode: Optional[str] = None, collect_stats_on_node_creation: bool = True,
             use_decompose_flow: bool = False) -> dict:
    db_name = sample["db"]
    question = sample["question"]
    schema_info = sample.get("ddl_data", sample.get("simplified_ddl", "")) #todo : change to ddl_data
    foreign_key = sample.get("foreign_key", "")
    # 只使用 evidence，不使用 combine_evidence（因为 combine_evidence 包含不需要的 "Evidence from other related questions" 部分）
    evidence_to_use = sample.get("evidence", "")

    db = build_db_connector(db_name)
    try:
        # 根据 multi_base_urls 构建 llm_config
        if multi_base_urls and len(multi_base_urls) > 0:
            # 使用多个端点：为每个端点创建一个配置项
            api_key = os.environ.get("VLLM_API_KEY", "dummy-key")
            config_list = []
            for base_url in multi_base_urls:
                # 为每个端点获取模型名称
                try:
                    model = pick_model(base_url, api_key)
                except Exception:
                    # 如果获取失败，使用环境变量或默认值
                    model = os.environ.get("VLLM_MODEL", "unknown")
                config_list.append({
                    "model": model,
                    "api_key": api_key,
                    "base_url": base_url
                })
            llm_config = {
                "config_list": config_list,
                "temperature": 0.2,  # 降低temperature以提高生成质量
            }
        else:
            # 使用单个端点（默认行为）
            llm_config = get_llm_config(temperature=0.2, auto_select=True)  # 降低temperature以提高生成质量

        # 使用parallel_workers参数设置MCTS内部的max_workers；mcts_v4 时开启问题拆分+子问题验证
        decompose_strategy = (mcts_config or {}).get("decompose_strategy", "S2")
        w = MCTSWorkflow(llm_config, db, max_workers=parallel_workers, strategy_mode=strategy_mode, collect_stats_on_node_creation=collect_stats_on_node_creation, use_decompose_flow=use_decompose_flow, decompose_strategy=decompose_strategy)

        # 应用MCTS配置（如果提供）
        if mcts_config:
            if 'rollouts_per_iteration' in mcts_config:
                w.rollouts_per_iteration = mcts_config['rollouts_per_iteration']
            if 'max_depth' in mcts_config:
                w.max_depth = mcts_config['max_depth']
                # 更新CTE生成器的max_depth
                w.cte_generator.max_depth = mcts_config['max_depth']
            if 'max_cte_nodes_per_iteration' in mcts_config:
                w.max_cte_nodes_per_iteration = mcts_config['max_cte_nodes_per_iteration']
            if 'num_sql_variants' in mcts_config:
                w.num_sql_variants = mcts_config['num_sql_variants']
            if 'use_column_suggestions' in mcts_config:
                w.sql_executor.use_column_suggestions = mcts_config['use_column_suggestions']

        res = w.solve(
            question=question,
            schema_info=f"db_name:{db_name}\n{schema_info}\nforeign_key:{foreign_key}",
            additional_context=f"{evidence_to_use}",
            qid=int(sample.get("question_id", sample.get("qid", 0)) or 0),
        )

        optimal_sql = res.get("optimal_sql", "")
        stats = res.get("statistics", {})
        all_sqls_with_attributes = res.get("all_sqls_with_attributes", [])
        rollout_stats = res.get("rollout_stats", [])  # 每个rollout的详细统计信息
        sub_questions = res.get("sub_questions", [])  # mcts_v4 问题拆分结果
        decompose_expand_traces = res.get("decompose_expand_traces") or []
        payload = {
            "sql": optimal_sql,
            "stats": stats,
            "all_sqls_with_attributes": all_sqls_with_attributes,
            "rollout_stats": rollout_stats,
            "sub_questions": sub_questions,
        }
        if decompose_expand_traces:
            payload["decompose_expand_traces"] = decompose_expand_traces
        for k in (
            "plan_proposals",
            "plan_dedup_count",
            "per_plan_rollout_stats",
            "union_rollout_stats",
            "column_binding_cot",
            "column_binding_cot_per_subq",
        ):
            if res.get(k) is not None:
                payload[k] = res[k]
        return payload
    finally:
        # 无论 solve 是否异常，都关闭连接，避免连接泄漏导致卡住
        if db is not None:
            db.disconnect()

def process_single_task(args_tuple):
    """处理单个任务的包装函数，用于并行执行"""
    idx, sample, parallel_workers, gold_sqls, ppls, multi_base_urls, mcts_config, strategy_mode, collect_stats_on_node_creation, use_decompose_flow = args_tuple
    try:
        qid = str(sample.get('question_id', idx))
        print(f">>> 样本#{idx} qid={qid} DB={sample['db']}")

        result = run_once(sample, parallel_workers=parallel_workers, multi_base_urls=multi_base_urls, mcts_config=mcts_config, strategy_mode=strategy_mode, collect_stats_on_node_creation=collect_stats_on_node_creation, use_decompose_flow=use_decompose_flow)


        # 与gold SQL对比（如果提供）
        predicted_sql = result['sql']
        gold_match = None
        gold_sql = None
        if gold_sqls and qid in gold_sqls:
            gold_sql = gold_sqls[qid]
            db_connector = build_db_connector(sample['db'])
            try:
                gold_match = compare_with_gold(predicted_sql, gold_sql, db_connector=db_connector)
                print(f"  qid={qid} gold_match={gold_match}")
            finally:
                if db_connector:
                    db_connector.disconnect()
        
        # 对所有SQL进行gold验证（用于相关性分析）
        all_sqls_with_gold = []
        if gold_sql and result.get('all_sqls_with_attributes'):
            db_connector = build_db_connector(sample['db'])
            try:
                for sql_info in result['all_sqls_with_attributes']:
                    sql = sql_info.get('sql', '')
                    if sql:
                        try:
                            sql_match = compare_with_gold(sql, gold_sql, db_connector=db_connector)
                            sql_info['is_correct'] = sql_match
                        except Exception:
                            sql_info['is_correct'] = False
                    else:
                        sql_info['is_correct'] = False
                    all_sqls_with_gold.append(sql_info)
            finally:
                if db_connector:
                    db_connector.disconnect()
        else:
            all_sqls_with_gold = result.get('all_sqls_with_attributes', [])
            for sql_info in all_sqls_with_gold:
                sql_info['is_correct'] = None  # 没有gold SQL，无法判断
        
        # 构建返回结果
        stats_obj = {
            'average_reward': result['stats'].get('average_reward', 0),
            'total_visits': result['stats'].get('total_visits', 0)
        }
        if isinstance(result.get('stats'), dict) and 'timing' in result['stats']:
            stats_obj['timing'] = result['stats']['timing']
        
        if gold_sqls and qid in gold_sqls:
            stats_obj['gold_match'] = gold_match
            stats_obj['gold_sql'] = gold_sqls[qid]
        
        ret = {
            'idx': idx,
            'qid': qid,
            'sql': result['sql'],
            'stats': stats_obj,
            'gold_match': gold_match,
            'all_sqls_with_attributes': all_sqls_with_gold,
            'rollout_stats': result.get('rollout_stats', []),
            'sub_questions': result.get('sub_questions', []),
        }
        if result.get('decompose_expand_traces'):
            ret['decompose_expand_traces'] = result['decompose_expand_traces']
        for k in (
            'plan_proposals',
            'plan_dedup_count',
            'per_plan_rollout_stats',
            'union_rollout_stats',
            'column_binding_cot',
            'column_binding_cot_per_subq',
        ):
            if result.get(k) is not None:
                ret[k] = result[k]
        from workflows.mcts_v4.utils.task_spill import delete_task_spill
        delete_task_spill(qid)
        return ret
    except Exception as e:
        print(f"❌ 样本#{idx} 处理失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            'idx': idx,
            'qid': str(sample.get('question_id', idx)),
            'sql': '',
            'stats': {},
            'gold_match': None,
            'error': str(e),
            'sub_questions': [],
        }


def _fast_fallback_task(task):
    """Single-rollout fallback when the main MCTS run exceeds the hard timeout."""
    idx, sample, parallel_workers, gold_sqls, ppls, multi_base_urls, mcts_config, strategy_mode, collect_stats, use_decompose = task
    fast_cfg = dict(mcts_config or {})
    fast_cfg['rollouts_per_iteration'] = 1
    fast_cfg['num_sql_variants'] = 1
    return (idx, sample, parallel_workers, gold_sqls, ppls, multi_base_urls, fast_cfg, strategy_mode, collect_stats, use_decompose)


# Spill recovery: match MCTS rollout SQL exec cap (single_workflow.sql_timeout_s)
SPILL_GOLD_COMPARE_TIMEOUT_S = float(os.environ.get("MCTS_SPILL_GOLD_COMPARE_TIMEOUT_S", "40"))


def _annotate_all_sqls_with_gold(all_sqls, gold_sql, db_name, *, timeout_s: Optional[float] = None):
    if not gold_sql or not all_sqls:
        return all_sqls
    db_connector = build_db_connector(db_name)
    try:
        for sql_info in all_sqls:
            sql = sql_info.get('sql', '')
            if sql:
                try:
                    sql_info['is_correct'] = compare_with_gold(
                        sql, gold_sql, db_connector=db_connector, timeout_s=timeout_s
                    )
                except Exception:
                    sql_info['is_correct'] = False
            else:
                sql_info['is_correct'] = False
    finally:
        db_connector.disconnect()
    return all_sqls


def _build_task_result_from_spill(task, spill: dict, timeout_s: int) -> Optional[dict]:
    """Re-select from spilled partial rollouts (R4/gated); gold only for eval labels."""
    from workflows.mcts_v4.utils.task_spill import (
        collect_all_sqls,
        select_sql_from_spill,
        spill_has_selectable_candidates,
    )

    idx, sample = task[0], task[1]
    gold_sqls = task[3]
    multi_base_urls = task[5]
    qid = str(sample.get('question_id', idx))
    rss = spill.get('rollout_stats') or []
    if not spill_has_selectable_candidates(rss):
        return None

    llm_config = None
    if multi_base_urls and len(multi_base_urls) > 0:
        api_key = os.environ.get("VLLM_API_KEY", "dummy-key")
        config_list = []
        for base_url in multi_base_urls:
            try:
                model = pick_model(base_url, api_key)
            except Exception:
                model = os.environ.get("VLLM_MODEL", "unknown")
            config_list.append({"model": model, "api_key": api_key, "base_url": base_url})
        llm_config = {"config_list": config_list, "temperature": 0.2}
    else:
        try:
            llm_config = get_llm_config(temperature=0.2, auto_select=True)
        except Exception:
            llm_config = None

    db_connector = build_db_connector(sample['db'])
    try:
        predicted_sql = select_sql_from_spill(
            spill, db_connector=db_connector, llm_config=llm_config
        )
    finally:
        db_connector.disconnect()

    if not (predicted_sql or "").strip():
        return None

    all_sqls = collect_all_sqls(rss)
    gold_sql = gold_sqls.get(qid) if gold_sqls else None
    spill_cmp_timeout = SPILL_GOLD_COMPARE_TIMEOUT_S
    if gold_sql:
        all_sqls = _annotate_all_sqls_with_gold(
            all_sqls, gold_sql, sample['db'], timeout_s=spill_cmp_timeout
        )

    gold_match = None
    if gold_sql:
        db_connector = build_db_connector(sample['db'])
        try:
            gold_match = compare_with_gold(
                predicted_sql, gold_sql, db_connector=db_connector, timeout_s=spill_cmp_timeout
            )
        finally:
            db_connector.disconnect()

    spill_stats = dict(spill.get('stats') or {})
    stats_obj = {
        'timeout_spill': True,
        'original_task_timeout_s': timeout_s,
        'rollout_count': len(rss),
    }
    if spill_stats.get('timing'):
        stats_obj['timing'] = spill_stats['timing']
    if gold_sqls and qid in gold_sqls:
        stats_obj['gold_match'] = gold_match
        stats_obj['gold_sql'] = gold_sqls[qid]

    print(f"  ↳ qid={qid} 使用 task spill（{len(rss)} rollout 候选）+ selector 重选")
    ret = {
        'idx': idx,
        'qid': qid,
        'sql': predicted_sql,
        'stats': stats_obj,
        'gold_match': gold_match,
        'all_sqls_with_attributes': all_sqls,
        'rollout_stats': rss,
        'sub_questions': spill.get('sub_questions') or [],
    }
    traces = spill.get('decompose_expand_traces') or []
    if traces:
        ret['decompose_expand_traces'] = traces
    return ret


def _empty_timeout_result(task, timeout_s: int, fallback_failed: bool = False) -> dict:
    idx, sample = task[0], task[1]
    qid = str(sample.get('question_id', idx))
    msg = f'任务超时（>{timeout_s}秒）'
    if fallback_failed:
        msg += '；fast fallback 也失败'
    return {
        'idx': idx,
        'qid': qid,
        'sql': '',
        'stats': {'gold_match': False, 'task_timeout': True, 'timeout_fallback_failed': fallback_failed},
        'gold_match': False,
        'error': msg,
        'all_sqls_with_attributes': [],
        'rollout_stats': [],
        'sub_questions': [],
    }


def _run_task_in_subprocess(task, timeout_s: int):
    """Run one sample in a child process; kill the worker on hard timeout."""
    qid = str(task[1].get('question_id', task[0]))
    with ProcessPoolExecutor(max_workers=1) as pool:
        future = pool.submit(process_single_task, task)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeoutError:
            print(f"⏱️ 样本 qid={qid} 硬超时 (>{timeout_s}s)，终止 worker")
            pool.shutdown(wait=False, cancel_futures=True)
            return None


def _resolve_task_after_timeout(task, timeout_s: int, fallback_timeout_s: int):
    """On timeout: partial spill + selector, else 1-rollout fallback, else empty record."""
    from workflows.mcts_v4.utils.task_spill import read_task_spill, task_spill_enabled

    qid = str(task[1].get('question_id', task[0]))
    if task_spill_enabled():
        spill = read_task_spill(qid)
        if spill:
            spilled = _build_task_result_from_spill(task, spill, timeout_s)
            if spilled and spilled.get('sql'):
                return spilled

    fb = _run_task_in_subprocess(_fast_fallback_task(task), fallback_timeout_s)
    if fb and fb.get('sql'):
        fb.setdefault('stats', {})
        if isinstance(fb['stats'], dict):
            fb['stats']['timeout_fallback'] = True
            fb['stats']['original_task_timeout_s'] = timeout_s
        print(f"  ↳ qid={fb.get('qid')} 使用 fast fallback（1 rollout）")
        return fb
    print(f"  ↳ qid={task[1].get('question_id', task[0])} fallback 无结果，写入 timeout 占位")
    return _empty_timeout_result(task, timeout_s, fallback_failed=True)


def _save_checkpoint(args, ppls, indices, processed_indices, results, results_with_stats):
    """Merge checkpoint JSON with any existing on-disk results."""
    if args.qids is not None:
        all_indices_for_output = sorted(processed_indices)
    else:
        all_indices_for_output = indices if args.index is not None else list(range(len(ppls)))

    if args.sql_out:
        Path(args.sql_out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.sql_out, 'w', encoding='utf-8') as fw:
            for j in all_indices_for_output:
                key = str(ppls[j].get('question_id', j))
                sql = results.get(key, "") if (j in processed_indices) else ""
                if sql:
                    sql = ' '.join(sql.split())
                fw.write(str(sql) + "\n")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        out_obj = {}
        if os.path.exists(args.json_out):
            try:
                with open(args.json_out, 'r', encoding='utf-8') as fr:
                    out_obj = json.load(fr)
            except Exception:
                pass
        for j in processed_indices:
            key = str(ppls[j].get('question_id', j))
            if key in results_with_stats:
                out_obj[key] = results_with_stats[key]
        with open(args.json_out, 'w', encoding='utf-8') as fw:
            json.dump(out_obj, fw, ensure_ascii=False, indent=2)


def _normalize_sample_mcts_v3(item: dict) -> dict:
    """将 mcts_v3 数据集格式（db_id, schema_prompt）转为 mcts_v1 所需格式（db, ddl_data, foreign_key）。"""
    if "db_id" not in item or "schema_prompt" not in item:
        return item
    schema_prompt = item.get("schema_prompt", "")
    if "foreign_key:" in schema_prompt:
        parts = schema_prompt.split("foreign_key:", 1)
        ddl_data = (parts[0].rstrip() or "").strip()
        foreign_key = (parts[1].strip() or "").strip()
    else:
        ddl_data = schema_prompt.strip()
        foreign_key = ""
    return {
        "db": item["db_id"],
        "question": item.get("question", ""),
        "evidence": item.get("evidence", ""),
        "ddl_data": ddl_data,
        "foreign_key": foreign_key,
        "question_id": item.get("question_id"),
        "SQL": item.get("SQL", ""),
    }


def load_sample(ppl_file: str, index: int) -> dict:
    with open(ppl_file, 'r', encoding='utf-8') as f:
        ppls = json.load(f)
    if index < 0 or index >= len(ppls):
        raise IndexError(f"index 超界: {index}/{len(ppls)}")
    item = ppls[index]
    # 支持 mcts_v3 格式：db_id + schema_prompt（arcwise_plat_sql_only_with_diff_withSchema.json）
    if item.get("db_id") and item.get("schema_prompt") is not None:
        item = _normalize_sample_mcts_v3(item)
    required_fields = ["db", "question", "ddl_data", "foreign_key"]
    missing = [k for k in required_fields if k not in item]
    if missing:
        raise KeyError(f"样本缺少字段: {missing}")
    return item


def load_gold_sqls(gold_file: str) -> dict:
    """加载gold SQL文件，返回 {question_id: gold_sql} 的字典"""
    gold_sqls = {}
    try:
        with open(gold_file, 'r', encoding='utf-8') as f:
            gold_data = json.load(f)
        for item in gold_data:
            qid = item.get('question_id')
            sql = item.get('SQL', '')
            if qid is not None:
                gold_sqls[str(qid)] = sql
    except Exception:
        pass
    return gold_sqls



def compare_with_gold(
    predicted_sql: str,
    gold_sql: str,
    db_connector: DatabaseConnector = None,
    *,
    timeout_s: Optional[float] = None,
) -> bool:
    """
    比较预测SQL和gold SQL的执行结果是否相同
    
    Args:
        predicted_sql: 预测的SQL
        gold_sql: 标准答案SQL
        db_connector: 数据库连接器（如果提供，则执行SQL比较结果；否则回退到字符串比较）
        timeout_s: 单条 SQL 执行超时（秒）；None 表示不限制
    
    Returns:
        bool: 如果结果匹配则为True，否则为False
    """
    # 如果提供了数据库连接器，执行SQL并比较结果
    if db_connector is not None:
        try:
            # 执行gold SQL - execute_query返回(DataFrame, error)或(None, error)
            gold_result, gold_error = db_connector.execute_query(gold_sql, timeout_s=timeout_s)
            # 执行predicted SQL
            predicted_result, predicted_error = db_connector.execute_query(
                predicted_sql, timeout_s=timeout_s
            )
            
            if gold_error is not None or predicted_error is not None or gold_result is None or predicted_result is None:
                return False
            
            # 比较结果（转换为集合进行比较，忽略顺序）
            import pandas as pd
            import numpy as np
            
            # 转换为字典列表格式（统一格式）
            def normalize_result(result):
                """将结果标准化为字典列表格式"""
                if result is None:
                    return []
                if isinstance(result, pd.DataFrame):
                    return result.to_dict('records')
                if isinstance(result, list):
                    # 如果是字典列表，直接返回
                    if result and isinstance(result[0], dict):
                        return result
                    # 如果是元组列表，转换为字典列表
                    if result and isinstance(result[0], (tuple, list)):
                        # 尝试获取列名
                        if hasattr(result, 'columns'):
                            columns = result.columns
                        else:
                            # 如果没有列名，使用索引
                            columns = [f'col_{i}' for i in range(len(result[0]))]
                        return [dict(zip(columns, row)) for row in result]
                return []
            
            gold_normalized = normalize_result(gold_result)
            predicted_normalized = normalize_result(predicted_result)
            
            if len(gold_normalized) == 0 and len(predicted_normalized) == 0:
                return True
            
            # 转换为可比较的格式（处理NaN、None等，忽略列名差异）
            def normalize_row(row):
                """标准化行数据，处理NaN、None等，忽略列名差异（只基于值）"""
                # 提取所有值（忽略列名），排序以确保稳定性
                values = []
                for k, v in row.items():
                    if pd.isna(v) or v is None:
                        values.append(None)
                    elif isinstance(v, (np.integer, np.floating)):
                        values.append(float(v) if isinstance(v, float) else int(v))
                    elif isinstance(v, (int, float)):
                        values.append(float(v) if isinstance(v, float) else int(v))
                    else:
                        # 字符串统一转换为小写并去除首尾空格
                        values.append(str(v).strip().lower())
                
                # 对值排序以确保稳定性（忽略列名和列的顺序）
                values.sort(key=lambda x: (
                    0 if x is None else 1,  # None在前
                    str(type(x).__name__),  # 按类型名排序
                    str(x) if x is not None else ''  # 按值排序
                ))
                return tuple(values)
            
            gold_set = {normalize_row(row) for row in gold_normalized}
            predicted_set = {normalize_row(row) for row in predicted_normalized}
            
            # 比较集合
            is_match = gold_set == predicted_set
            
            return is_match
            
        except Exception:
            return False
    return False


def main():
    parser = argparse.ArgumentParser(description="并行 rollout 测试")
    parser.add_argument("--ppl_file", type=str, required=True, help="样本文件（JSON 数组）")
    parser.add_argument("--index", type=int, default=None, help="只跑第 index 个样本（可选）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 个样本（全量时生效，与 --qid/--qids/--index 互斥）")
    parser.add_argument("--qid", type=str, default=None, help="按 question_id 精确定位并只跑该条（优先于 --index）")
    parser.add_argument("--qids", type=str, default=None, help="多个 question_id，用逗号分隔，如 '29,31,32'（优先于 --qid）")
    parser.add_argument("--qids_file", type=str, default=None,
                       help="JSON 文件，含 qids 列表或 {qids: [...]}（优先于 --qids）")
    parser.add_argument("--random_seed", type=int, default=20240601,
                       help="固定 random/numpy seed（默认 20240601）")
    parser.add_argument("--gold_file", type=str, default=None, help="Gold SQL文件路径（用于验证）")
    parser.add_argument("--sql_out", type=str, default=None, help="SQL输出TXT")
    parser.add_argument("--json_out", type=str, default=None, help="保存结果的JSON")
    parser.add_argument("--parallel_workers", type=int, default=5, help="MCTS内部并行工作线程数（用于CTE/SQL生成，默认5）")
    parser.add_argument("--max_workers", type=int, default=1, help="并行处理多个问题的工作线程数（默认1）")
    parser.add_argument("--multi_base_urls", type=str, default=None, help="多个模型端点URL，用逗号分隔，例如：'http://localhost:8009/v1,http://localhost:8010/v1'")
    parser.add_argument("--max_cte_nodes", type=int, default=5, help="每次扩展节点时生成的CTE变体数量（默认3）")
    parser.add_argument("--max_depth", type=int, default=None, help="MCTS树最大深度/CTE最大步数（默认8，如果提供则覆盖）")
    parser.add_argument("--rollouts_per_iteration", type=int, default=8, help="每次迭代的rollout数量（默认8）")
    parser.add_argument("--num_sql_variants", type=int, default=6, help="每个rollout末尾生成的SQL变体数量（默认6）")
    parser.add_argument("--strategy_mode", type=str, default=None, 
                       help="策略模式：FORCE_S1/S2/S3/S4/S5, NONE, LLM_PICK_ONCE（默认None，使用全局配置FORCE_S4）")
    parser.add_argument("--task_timeout", type=int, default=600,
                       help="单个任务的最大超时时间（秒），默认600秒（10分钟）。超时后尝试 1-rollout fallback")
    parser.add_argument("--timeout_fallback_secs", type=int, default=180,
                       help="主任务硬超时后的 fast fallback 时限（秒），默认180")
    parser.add_argument("--task_spill_interval", type=int, default=90,
                       help="task spill 心跳写盘间隔（秒），默认90；设 0 则仅每 rollout 写")
    parser.add_argument("--no_task_spill", action="store_true",
                       help="禁用 .task_spill 部分 rollout 超时恢复")
    parser.add_argument("--skip_processed", action="store_true",
                       help="跳过已处理的问题（检查JSON输出文件中是否已有结果）")
    parser.add_argument("--collect_stats", action="store_true", default=True,
                       help="在节点创建时收集统计信息（默认True）")
    parser.add_argument("--no_collect_stats", action="store_true",
                       help="不在节点创建时收集统计信息（与--collect_stats互斥）")
    parser.add_argument("--db_root", type=str, default=None,
                       help="数据库根目录（如 mcts_v3 的 bird_db_root），未设时使用环境变量 DB_ROOT_DIR 或默认相对路径")
    parser.add_argument("--use_decompose_flow", action="store_true",
                       help="使用 mcts_v4 流程：问题拆分 + 子问题验证 + 类 Alpha-SQL Select/Expand/Simulate")
    parser.add_argument("--decompose_strategy", type=str, default="S2",
                       choices=["S1", "S2", "S3", "S4", "S7"],
                       help="问题拆分策略：S1 Entity-First / S2 Relation-First / S3 Evidence-Based / S4 Clause-Order / S7 Grain/Key-Based，仅 --use_decompose_flow 时生效")
    parser.add_argument("--no_column_suggestions", action="store_true",
                       help="列名错误修复时不加入相似列推荐（用于 A/B：与默认带 suggestions 对比）")
    args = parser.parse_args()

    random.seed(args.random_seed)
    np.random.seed(args.random_seed)

    if args.json_out and not args.no_task_spill:
        from workflows.mcts_v4.utils.task_spill import spill_dir_from_json_out

        spill_dir = spill_dir_from_json_out(args.json_out)
        spill_dir.mkdir(parents=True, exist_ok=True)
        os.environ["MCTS_TASK_SPILL_DIR"] = str(spill_dir)
        os.environ["MCTS_TASK_SPILL"] = "1"
        if args.task_spill_interval > 0:
            os.environ["MCTS_TASK_SPILL_INTERVAL_S"] = str(args.task_spill_interval)
    elif args.no_task_spill:
        os.environ["MCTS_TASK_SPILL"] = "0"

    if args.qids_file:
        with open(args.qids_file, "r", encoding="utf-8") as f:
            qf = json.load(f)
        qid_list = qf.get("qids", qf) if isinstance(qf, dict) else qf
        args.qids = ",".join(str(q) for q in qid_list)

    # 使用 mcts_v3 数据集时自动设置 DB 根目录（与 mcts_v3 config 一致）
    if args.db_root:
        os.environ["DB_ROOT_DIR"] = args.db_root
    elif not os.environ.get("DB_ROOT_DIR"):
        _ppl_path = Path(args.ppl_file)
        if _ppl_path.exists():
            try:
                with open(_ppl_path, 'r', encoding='utf-8') as f:
                    first = json.load(f)
                if isinstance(first, list) and len(first) > 0 and first[0].get("db_id") and first[0].get("schema_prompt") is not None:
                    _mcts_v3_config = Path(__file__).resolve().parent.parent.parent / "mcts_v3" / "config.yaml"
                    if _mcts_v3_config.exists():
                        import yaml
                        with open(_mcts_v3_config) as cf:
                            _cfg = yaml.safe_load(cf)
                        _bird_root = (_cfg.get("data") or {}).get("bird_db_root")
                        if _bird_root:
                            os.environ["DB_ROOT_DIR"] = _bird_root
            except Exception:
                pass

    # MCTS配置
    mcts_config = {
        'max_cte_nodes_per_iteration': args.max_cte_nodes,
        'rollouts_per_iteration': args.rollouts_per_iteration,
        'num_sql_variants': args.num_sql_variants,
        'decompose_strategy': args.decompose_strategy,
        'use_column_suggestions': not args.no_column_suggestions,
    }
    if args.max_depth is not None:
        mcts_config['max_depth'] = args.max_depth

    # 处理统计信息收集配置
    collect_stats_on_node_creation = args.collect_stats and not args.no_collect_stats

    # 解析多模型端点
    multi_base_urls = None
    if args.multi_base_urls:
        multi_base_urls = [url.strip() for url in args.multi_base_urls.split(',') if url.strip()]
    else:
        env_url = os.environ.get("VLLM_API_URL")
        multi_base_urls = [env_url.strip()] if env_url else None

    # 加载gold SQL（如果提供）
    gold_sqls = {}
    if args.gold_file:
        gold_sqls = load_gold_sqls(args.gold_file)

    with open(args.ppl_file, 'r', encoding='utf-8') as f:
        ppls = json.load(f)

    # 优先级：--qids > --qid > --index > 全量
    if args.qids is not None:
        # 解析多个qid
        qid_list = [q.strip() for q in args.qids.split(',') if q.strip()]
        indices = []
        qid_to_idx = {}
        for i, item in enumerate(ppls):
            qid_val = item.get('question_id', None)
            if qid_val is not None:
                qid_str = str(qid_val)
                qid_to_idx[qid_str] = i
        
        for qid in qid_list:
            if qid in qid_to_idx:
                indices.append(qid_to_idx[qid])
            else:
                pass
        if not indices:
            raise ValueError(f"未找到任何指定的 question_id: {args.qids}")
    elif args.qid is not None:
        target_idx = None
        for i, item in enumerate(ppls):
            qid_val = item.get('question_id', None)
            if qid_val is None:
                continue
            # 支持字符串/整数两种形式的等价匹配
            try:
                if str(qid_val) == str(args.qid):
                    target_idx = i
                    break
            except Exception:
                pass
        if target_idx is None:
            raise ValueError(f"未在 {args.ppl_file} 中找到 question_id={args.qid} 的样本")
        indices = [target_idx]
    else:
        # 若指定 index，则仅跑该样本；否则全量或按 limit 取前 N 个
        if args.index is not None:
            indices = [args.index]
        elif args.limit is not None:
            indices = list(range(min(args.limit, len(ppls))))
        else:
            indices = list(range(len(ppls)))

    results = {}
    results_with_stats = {}  # 保存完整的统计信息
    processed_indices = []
    correct_count = 0
    total_count = 0

    # 检查已处理的样本（如果启用跳过功能）
    skipped_indices = []
    if args.skip_processed and args.json_out and os.path.exists(args.json_out):
        try:
            with open(args.json_out, 'r', encoding='utf-8') as f:
                existing_results = json.load(f)
            for idx in indices:
                qid = str(ppls[idx].get('question_id', idx))
                if qid in existing_results:
                    skipped_indices.append(idx)
                    # 将现有结果加载到内存中
                    results[qid] = existing_results[qid]['sql']
                    results_with_stats[qid] = existing_results[qid]
                    processed_indices.append(idx)
                    # 统计gold验证结果
                    if existing_results[qid].get('stats', {}).get('gold_match') is not None:
                        total_count += 1
                        if existing_results[qid]['stats']['gold_match']:
                            correct_count += 1
        except Exception:
            pass

    # 准备任务列表（排除已处理的样本）
    tasks = []
    remaining_indices = [idx for idx in indices if idx not in skipped_indices]
    for idx in remaining_indices:
        sample = load_sample(args.ppl_file, idx)
        tasks.append((idx, sample, args.parallel_workers, gold_sqls, ppls, multi_base_urls, mcts_config, args.strategy_mode, collect_stats_on_node_creation, args.use_decompose_flow))
    
    # 逐题子进程 + 硬超时：避免 ThreadPool 超时后 worker 线程仍占用 max_workers=1 导致整 shard 卡死
    print(f"处理 {len(tasks)} 个样本 (sequential subprocess, task_timeout={args.task_timeout}s)")
    save_lock = threading.Lock()
    completed_count = 0

    for task in tqdm(tasks, desc="处理样本"):
        try:
            result_dict = _run_task_in_subprocess(task, args.task_timeout)
            if result_dict is None:
                result_dict = _resolve_task_after_timeout(task, args.task_timeout, args.timeout_fallback_secs)

            idx = result_dict['idx']
            qid = result_dict['qid']
            results[qid] = result_dict['sql']
            results_with_stats[qid] = {
                'sql': result_dict['sql'],
                'optimal_sql': result_dict['sql'],
                'stats': result_dict['stats'],
                'all_sqls_with_attributes': result_dict.get('all_sqls_with_attributes', []),
                'rollout_stats': result_dict.get('rollout_stats', []),
                'sub_questions': result_dict.get('sub_questions', []),
            }
            if result_dict.get('decompose_expand_traces'):
                results_with_stats[qid]['decompose_expand_traces'] = result_dict['decompose_expand_traces']
            for k in (
                'plan_proposals',
                'plan_dedup_count',
                'per_plan_rollout_stats',
                'union_rollout_stats',
                'column_binding_cot',
                'column_binding_cot_per_subq',
            ):
                if result_dict.get(k) is not None:
                    results_with_stats[qid][k] = result_dict[k]
            if result_dict.get('error'):
                results_with_stats[qid]['error'] = result_dict['error']
            processed_indices.append(idx)
            completed_count += 1

            if result_dict.get('gold_match') is not None:
                total_count += 1
                if result_dict['gold_match']:
                    correct_count += 1

            with save_lock:
                _save_checkpoint(args, ppls, indices, processed_indices, results, results_with_stats)
        except Exception as e:
            print(f"❌ 处理任务出错: {e}")
            import traceback
            traceback.print_exc()
    
    # 打印最终统计
    if gold_sqls and total_count > 0:
        print(f"Gold验证: {correct_count}/{total_count} 正确 ({100*correct_count/total_count:.2f}%)")


if __name__ == "__main__":
    main()


# 使用示例（从项目根目录运行，使用相对路径）
# 
# 基本用法：
# python workflows/mcts_v1/test/test_mcts.py \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --sql_out workflows/mcts_v1/test/out/test_single.txt \
#   --json_out workflows/mcts_v1/test/out/test_single.json \
#   --qid 25 \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5
#
# 多模型并行：
# python workflows/mcts_v1/test/test_mcts.py \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --sql_out workflows/mcts_v1/test/out/12_6all.txt \
#   --json_out workflows/mcts_v1/test/out/test_single.json \
#   --qid 25 \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5 \
#   --multi_base_urls "http://localhost:8011/v1,http://localhost:8009/v1"
#
# 后台运行示例：
# nohup python workflows/mcts_v1/test/test_mcts.py \
#   --max_workers 10 \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --sql_out workflows/mcts_v1/test/out/12_11.txt \
#   --json_out workflows/mcts_v1/test/out/12_11.json \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 15 \
#   --multi_base_urls "http://localhost:8009/v1,http://localhost:8011/v1,http://localhost:8012/v1" \
#   --max_cte_nodes 15 \
#   > workflows/mcts_v1/test/out/12_11.log 2>&1 &
#
# 多个问题ID测试：
# python workflows/mcts_v1/test/test_mcts.py \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --qids "81,287,479" \
#   --sql_out workflows/mcts_v1/test/out/test_q81_287_479_sql.txt \
#   --json_out workflows/mcts_v1/test/out/test_q81_287_479_result.json \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5 \
#   --max_workers 1 \
#   --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1"
#
# 策略模式测试：
# python workflows/mcts_v1/test/test_mcts.py \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --sql_out workflows/mcts_v1/test/out/1_6_test_no_strategy_sql.txt \
#   --json_out workflows/mcts_v1/test/out/1_6_test_no_strategy_result.json \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5 \
#   --strategy_mode NONE \
#   --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1"


