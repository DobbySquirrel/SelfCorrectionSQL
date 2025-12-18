"""
并行 rollout 测试（参考 test_ab_entropy_backprop 风格）

使用并行 rollout 模式运行 MCTS，默认启用熵回传。

从 --ppl_file 读取样本（与 test_mcts_workflow 的样本格式一致）。
"""

import json
import sys
import argparse
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from workflows.mcts.mcts_workflow import MCTSWorkflow
from workflows.mcts.utils.evidence_filter import filter_and_combine_evidence
from core.database_connector import DatabaseConnector
from utils.model_utils import get_llm_config, print_model_info
import logging
logging.getLogger("autogen.oai.client").setLevel(logging.ERROR)


def build_db_connector(db_name: str) -> DatabaseConnector:
    db_path = f"/ssd/shenshuyu/work/bird/dev_20240627/dev_databases/{db_name}/{db_name}.sqlite"
    db_connector = DatabaseConnector(db_path)
    if not db_connector.connect():
        raise RuntimeError(f"数据库连接失败: {db_path}")
    return db_connector


def run_once(sample: dict, parallel_workers: int = 5) -> dict:
    db_name = sample["db"]
    question = sample["question"]
    schema_info = sample["simplified_ddl"]
    foreign_key = sample.get("foreign_key", "")
    evidence = sample.get("combine_evidence", "")
    single_evidence = sample.get("evidence", "")

    db = build_db_connector(db_name)
    llm_config = get_llm_config(temperature=0.7, auto_select=True)

    # # 在调用solve之前，使用LLM过滤和组合evidence
    # filtered_evidence = ""
    # if evidence:
    #     try:
    #         filtered_evidence = filter_and_combine_evidence(
    #             simplified_ddl=f"db_name:{db_name}\n{schema_info}\nforeign_key:{foreign_key}\n Hint you must use:{single_evidence}",
    #             question=question,
    #             combine_evidence=evidence,
    #             llm_config=llm_config
    #         )
           
    #         if "</think>" in filtered_evidence:
    #             filtered_evidence = filtered_evidence.split("</think>")[1]
    #         if filtered_evidence:
    #             print(f"[Evidence过滤] 过滤完成，提取了 {len(filtered_evidence)} 个字符的有用信息")
    #         else:
    #             print(f"[Evidence过滤] 未找到有用的evidence信息")
    #     except Exception as e:
    #         print(f"[警告] Evidence过滤失败，使用原始evidence: {e}")
    #         filtered_evidence = evidence
    # else:
    #     print(f"[Evidence过滤] 没有evidence需要过滤")

    w = MCTSWorkflow(llm_config, db)
    w.use_parallel_rollouts = True  # 强制使用并行模式
    w.parallel_rollout_workers = parallel_workers
    
    # 默认使用熵回传
    w.use_entropy_backprop = True
    w.entropy_K = 1
    w.entropy_eps = 1e-6
    # 记录执行时间
    start_time = time.time()
    res = w.solve(
        question=question,
        schema_info=f"db_name:{db_name}\n{schema_info}\nforeign_key:{foreign_key}",
        additional_context=f"{single_evidence}"
    )
    elapsed_time = time.time() - start_time

    optimal_sql = res.get("optimal_sql", "")
    stats = res.get("statistics", {})
    db.disconnect()

    return {
        "sql": optimal_sql,
        "stats": stats,
        "elapsed_time": elapsed_time,
    }


def process_single_task(args_tuple):
    """处理单个任务的包装函数，用于并行执行"""
    idx, sample, parallel_workers, gold_sqls, ppls = args_tuple
    try:
        qid = str(sample.get('question_id', idx))
        print(f"\n{'='*80}")
        print(f">>> 样本#{idx} (question_id={qid}) | DB={sample['db']}")
        print(f"{'='*80}")

        result = run_once(sample, parallel_workers=parallel_workers)

        # 打印摘要
        print(f"\n[样本#{idx}] 结果 | avg_reward={result['stats'].get('average_reward', 0):.6f}  "
              f"total_visits={result['stats'].get('total_visits', 0)}  "
              f"time={result['elapsed_time']:.2f}s")
        # 打印分阶段计时（若存在）
        timing = result['stats'].get('timing', {}) if isinstance(result.get('stats'), dict) else {}
        if timing:
            print(f"[样本#{idx}] 阶段耗时(秒): ")
            print(f"  total={timing.get('total_s', 0):.2f}  rollout={timing.get('rollout_s', 0):.2f}  "
                  f"cte_gen={timing.get('cte_gen_s', 0):.2f}  sql_gen={timing.get('sql_gen_s', 0):.2f}  db_exec={timing.get('db_exec_s', 0):.2f}  "
                  f"rollouts={timing.get('rollout_count', 0)}")
        
        # 与gold SQL对比（如果提供）
        predicted_sql = result['sql']
        gold_match = None
        if gold_sqls and qid in gold_sqls:
            gold_sql = gold_sqls[qid]
            gold_match = compare_with_gold(predicted_sql, gold_sql)
            if gold_match:
                print(f"\n✅ [样本#{idx}] [Gold验证] question_id={qid}: 匹配成功！")
            else:
                print(f"\n❌ [样本#{idx}] [Gold验证] question_id={qid}: 不匹配")
        elif gold_sqls:
            print(f"\n⚠️ [样本#{idx}] [Gold验证] question_id={qid}: 未找到对应的gold SQL")
        
        # 构建返回结果
        stats_obj = {
            'average_reward': result['stats'].get('average_reward', 0),
            'total_visits': result['stats'].get('total_visits', 0),
            'elapsed_time': result['elapsed_time'],
        }
        if isinstance(result.get('stats'), dict) and 'timing' in result['stats']:
            stats_obj['timing'] = result['stats']['timing']
        
        if gold_sqls and qid in gold_sqls:
            stats_obj['gold_match'] = gold_match
            stats_obj['gold_sql'] = gold_sqls[qid]
        
        return {
            'idx': idx,
            'qid': qid,
            'sql': result['sql'],
            'stats': stats_obj,
            'gold_match': gold_match,
        }
    except Exception as e:
        print(f"\n❌ [样本#{idx}] 处理失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            'idx': idx,
            'qid': str(sample.get('question_id', idx)),
            'sql': '',
            'stats': {},
            'gold_match': None,
            'error': str(e),
        }


def load_sample(ppl_file: str, index: int) -> dict:
    with open(ppl_file, 'r', encoding='utf-8') as f:
        ppls = json.load(f)
    if index < 0 or index >= len(ppls):
        raise IndexError(f"index 超界: {index}/{len(ppls)}")
    required_fields = ["db", "question", "simplified_ddl", "foreign_key"]
    missing = [k for k in required_fields if k not in ppls[index]]
    if missing:
        raise KeyError(f"样本缺少字段: {missing}")
    return ppls[index]


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
        print(f"[Gold] 从 {gold_file} 加载了 {len(gold_sqls)} 条gold SQL")
    except Exception as e:
        print(f"[警告] 加载gold文件失败: {e}")
    return gold_sqls


def normalize_sql_for_comparison(sql: str) -> str:
    """标准化SQL用于比较（去除空白、转小写等）"""
    if not sql:
        return ""
    # 去除多余空白，转小写
    normalized = ' '.join(sql.split()).lower().strip()
    # 去除末尾分号
    if normalized.endswith(';'):
        normalized = normalized[:-1].strip()
    return normalized


def compare_with_gold(predicted_sql: str, gold_sql: str) -> bool:
    """比较预测SQL和gold SQL是否相同"""
    pred_norm = normalize_sql_for_comparison(predicted_sql)
    gold_norm = normalize_sql_for_comparison(gold_sql)
    return pred_norm == gold_norm


def main():
    parser = argparse.ArgumentParser(description="并行 rollout 测试")
    parser.add_argument("--ppl_file", type=str, required=True, help="样本文件（JSON 数组）")
    parser.add_argument("--index", type=int, default=None, help="只跑第 index 个样本（可选）")
    parser.add_argument("--qid", type=str, default=None, help="按 question_id 精确定位并只跑该条（优先于 --index）")
    parser.add_argument("--qids", type=str, default=None, help="多个 question_id，用逗号分隔，如 '29,31,32'（优先于 --qid）")
    parser.add_argument("--gold_file", type=str, default=None, help="Gold SQL文件路径（用于验证）")
    parser.add_argument("--sql_out", type=str, default=None, help="SQL输出TXT")
    parser.add_argument("--json_out", type=str, default=None, help="保存结果的JSON")
    parser.add_argument("--parallel_workers", type=int, default=5, help="并行rollout的工作线程数（默认5）")
    parser.add_argument("--max_workers", type=int, default=1, help="并行处理多个问题的工作线程数（默认1，即串行）")
    args = parser.parse_args()

    print(f"并行rollout工作线程数: {args.parallel_workers}")
    print(f"并行处理问题数: {args.max_workers}")
    print_model_info()

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
                print(f"[警告] 未找到 question_id={qid} 的样本，跳过")
        
        if not indices:
            raise ValueError(f"未找到任何指定的 question_id: {args.qids}")
        print(f"定位到 {len(indices)} 个样本: question_id={args.qids}")
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
        print(f"定位到 question_id={args.qid} 于索引 {target_idx}")
    else:
        # 若指定 index，则仅跑该样本
        indices = [args.index] if args.index is not None else list(range(len(ppls)))

    results = {}
    results_with_stats = {}  # 保存完整的统计信息
    processed_indices = []
    correct_count = 0
    total_count = 0
    
    # 准备任务列表
    tasks = []
    for idx in indices:
        sample = load_sample(args.ppl_file, idx)
        tasks.append((idx, sample, args.parallel_workers, gold_sqls, ppls))
    
    # 如果max_workers为1，使用串行处理（保持原有行为）
    if args.max_workers == 1:
        print(f"\n使用串行模式处理 {len(tasks)} 个样本...")
        for task in tasks:
            idx, sample, parallel_workers, gold_sqls, ppls = task
            result_dict = process_single_task(task)
            
            idx = result_dict['idx']
            qid = result_dict['qid']
            results[qid] = result_dict['sql']
            results_with_stats[qid] = {
                'sql': result_dict['sql'],
                'stats': result_dict['stats'],
            }
            processed_indices.append(idx)
            
            # 统计gold验证结果
            if result_dict.get('gold_match') is not None:
                total_count += 1
                if result_dict['gold_match']:
                    correct_count += 1
            
            # 每步即时保存
            if args.qids is not None:
                all_indices_for_output = processed_indices
            else:
                all_indices_for_output = indices if args.index is not None else list(range(len(ppls)))

            # 保存TXT
            if args.sql_out:
                Path(args.sql_out).parent.mkdir(parents=True, exist_ok=True)
                with open(args.sql_out, 'w', encoding='utf-8') as fw:
                    for j in all_indices_for_output:
                        key = str(ppls[j].get('question_id', j))
                        sql = results.get(key, "") if (j in processed_indices) else ""
                        if sql:
                            sql = ' '.join(sql.split())
                        fw.write(str(sql) + "\n")
                print(f"[保存] SQL -> {args.sql_out} (已处理 {len(processed_indices)}/{len(all_indices_for_output)})")

            # 保存JSON
            if args.json_out:
                Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
                out_obj = {}
                for j in processed_indices:
                    key = str(ppls[j].get('question_id', j))
                    out_obj[key] = results_with_stats.get(key, {'sql': '', 'stats': {}})
                with open(args.json_out, 'w', encoding='utf-8') as fw:
                    json.dump(out_obj, fw, ensure_ascii=False, indent=2)
                print(f"[保存] JSON -> {args.json_out} (已处理 {len(processed_indices)})")
    else:
        # 并行处理模式
        print(f"\n使用并行模式处理 {len(tasks)} 个样本（{args.max_workers} 个worker）...")
        save_lock = threading.Lock()
        
        # 使用线程池并行处理
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            future_to_task = {executor.submit(process_single_task, task): task for task in tasks}
            
            completed_count = 0
            for future in tqdm(as_completed(future_to_task), total=len(tasks), desc="处理样本"):
                try:
                    result_dict = future.result()
                    
                    idx = result_dict['idx']
                    qid = result_dict['qid']
                    results[qid] = result_dict['sql']
                    results_with_stats[qid] = {
                        'sql': result_dict['sql'],
                        'stats': result_dict['stats'],
                    }
                    processed_indices.append(idx)
                    completed_count += 1
                    
                    # 统计gold验证结果
                    if result_dict.get('gold_match') is not None:
                        total_count += 1
                        if result_dict['gold_match']:
                            correct_count += 1
                    
                    # 每完成一个任务或全部完成时保存一次
                    if completed_count % 1 == 0 or completed_count == len(tasks):
                        with save_lock:
                            # 确定输出索引
                            if args.qids is not None:
                                all_indices_for_output = sorted(processed_indices)
                            else:
                                all_indices_for_output = indices if args.index is not None else list(range(len(ppls)))

                            # 保存TXT
                            if args.sql_out:
                                Path(args.sql_out).parent.mkdir(parents=True, exist_ok=True)
                                with open(args.sql_out, 'w', encoding='utf-8') as fw:
                                    for j in all_indices_for_output:
                                        key = str(ppls[j].get('question_id', j))
                                        sql = results.get(key, "") if (j in processed_indices) else ""
                                        if sql:
                                            sql = ' '.join(sql.split())
                                        fw.write(str(sql) + "\n")
                                print(f"[保存] SQL -> {args.sql_out} (已处理 {len(processed_indices)}/{len(all_indices_for_output)})")

                            # 保存JSON
                            if args.json_out:
                                Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
                                out_obj = {}
                                for j in processed_indices:
                                    key = str(ppls[j].get('question_id', j))
                                    out_obj[key] = results_with_stats.get(key, {'sql': '', 'stats': {}})
                                with open(args.json_out, 'w', encoding='utf-8') as fw:
                                    json.dump(out_obj, fw, ensure_ascii=False, indent=2)
                                print(f"[保存] JSON -> {args.json_out} (已处理 {len(processed_indices)})")
                            
                except Exception as e:
                    print(f"\n❌ 处理任务时出错: {e}")
                    import traceback
                    traceback.print_exc()
    
    # 打印最终统计
    if gold_sqls and total_count > 0:
        print(f"\n{'='*80}")
        print(f"[最终统计] Gold验证: {correct_count}/{total_count} 正确 (准确率: {correct_count/total_count*100:.2f}%)")
        print(f"{'='*80}\n")


if __name__ == "__main__":
    # 示例：
    main()
    
    # 单个qid示例：
    # python workflows/mcts/test/test_bc_parallel_rollout_backup11_5.py \
    #   --ppl_file data/subset_ppl_dev_python.json \
    #   --sql_out workflows/mcts/test/out/single.txt \
    #   --json_out workflows/mcts/test/out/single.json \
    #   --qid 31 \
    #   --gold_file /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json \
    #   --parallel_workers 5
    
    # 多个qid示例（测试29,31,32）：
# python /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/test_bc_parallel_rollout_backup11_5.py \
#   --ppl_file /home/shenshuyu/SQL_tool_multiAgent/data/subset_ppl_dev_python.json \
#   --sql_out /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/single_860_timing.txt \
#   --json_out /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/single_860_timing.json \
#   --qids "860" \
#   --gold_file /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5
    
    # 并行处理多个问题示例（使用4个worker同时处理）：
    # python workflows/mcts/test/test_bc_parallel_rollout_backup11_5.py \
    #   --ppl_file data/subset_ppl_dev_python.json \
    #   --sql_out workflows/mcts/test/out/parallel.txt \
    #   --json_out workflows/mcts/test/out/parallel.json \
    #   --qids "29,31,32,33,34" \
    #   --gold_file data/sub_sampled_bird_dev_set.json \
    #   --parallel_workers 5 \
    #   --max_workers 4