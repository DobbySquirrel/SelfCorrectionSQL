#!/usr/bin/env python3
"""
从 Alpha-SQL 运行结果中提取 SQL

使用方法:
    # 提取单个文件
    python extract_sql_from_results.py --pkl_file results/Qwen_Qwen3-8B/dev/0.pkl
    
    # 提取整个目录的所有结果
    python extract_sql_from_results.py --results_dir results/Qwen_Qwen3-8B/dev
    
    # 提取并保存为 JSON
    python extract_sql_from_results.py --results_dir results/Qwen_Qwen3-8B/dev --output results/pred_sqls.json
"""

import pickle
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional


def extract_sql_from_reasoning_paths(reasoning_paths: List[List[Any]]) -> List[Dict[str, Any]]:
    """
    从推理路径中提取 SQL
    
    Args:
        reasoning_paths: 推理路径列表，每个路径是一个节点列表
        
    Returns:
        包含 SQL 信息的字典列表
    """
    sql_results = []
    
    for path_idx, path in enumerate(reasoning_paths):
        # 路径的最后一个节点通常是 END 节点，包含 final_sql_query
        if not path:
            continue
            
        end_node = path[-1]
        
        # 提取 SQL（优先使用 final_sql_query，然后是 revised_sql_query，最后是 sql_query）
        sql = None
        if hasattr(end_node, 'final_sql_query') and end_node.final_sql_query:
            sql = end_node.final_sql_query
        elif hasattr(end_node, 'revised_sql_query') and end_node.revised_sql_query:
            sql = end_node.revised_sql_query
        elif hasattr(end_node, 'sql_query') and end_node.sql_query:
            sql = end_node.sql_query
        else:
            # 如果 END 节点没有 SQL，尝试从前一个节点获取
            if len(path) > 1:
                prev_node = path[-2]
                if hasattr(prev_node, 'revised_sql_query') and prev_node.revised_sql_query:
                    sql = prev_node.revised_sql_query
                elif hasattr(prev_node, 'sql_query') and prev_node.sql_query:
                    sql = prev_node.sql_query
        
        # 提取其他信息
        question_id = None
        db_id = None
        question = None
        
        if path:
            root_node = path[0]
            if hasattr(root_node, 'db_id'):
                db_id = root_node.db_id
            if hasattr(root_node, 'original_question'):
                question = root_node.original_question
        
        # 尝试从节点中获取 question_id（如果有的话）
        for node in path:
            if hasattr(node, 'question_id') and node.question_id is not None:
                question_id = node.question_id
                break
        
        sql_results.append({
            'path_index': path_idx,
            'question_id': question_id,
            'db_id': db_id,
            'question': question,
            'sql': sql,
            'path_length': len(path)
        })
    
    return sql_results


def extract_sql_from_pkl_file(pkl_file: Path) -> Dict[str, Any]:
    """
    从单个 .pkl 文件提取 SQL
    
    Args:
        pkl_file: .pkl 文件路径
        
    Returns:
        包含提取结果的字典
    """
    with open(pkl_file, 'rb') as f:
        reasoning_paths = pickle.load(f)
    
    question_id = int(pkl_file.stem)
    sql_results = extract_sql_from_reasoning_paths(reasoning_paths)
    
    # 选择最佳 SQL（通常选择第一个，或者可以根据需要选择其他策略）
    best_sql = None
    if sql_results:
        # 优先选择有 SQL 的结果
        for result in sql_results:
            if result['sql']:
                best_sql = result['sql']
                break
    
    return {
        'question_id': question_id,
        'num_paths': len(reasoning_paths),
        'best_sql': best_sql,
        'all_paths': sql_results
    }


def extract_sql_from_directory(results_dir: Path, output_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    从目录中的所有 .pkl 文件提取 SQL
    
    Args:
        results_dir: 结果目录路径
        output_file: 输出 JSON 文件路径（可选）
        
    Returns:
        所有任务的 SQL 结果列表
    """
    pkl_files = sorted(results_dir.glob("*.pkl"), key=lambda x: int(x.stem) if x.stem.isdigit() else 0)
    
    all_results = []
    
    print(f"找到 {len(pkl_files)} 个结果文件")
    print("正在提取 SQL...")
    
    for pkl_file in pkl_files:
        try:
            result = extract_sql_from_pkl_file(pkl_file)
            all_results.append(result)
            
            if result['best_sql']:
                print(f"✓ question_id={result['question_id']}: 提取成功 ({result['num_paths']} 条路径)")
            else:
                print(f"⚠ question_id={result['question_id']}: 未找到 SQL ({result['num_paths']} 条路径)")
        except Exception as e:
            print(f"✗ {pkl_file.name}: 提取失败 - {e}")
    
    # 按 question_id 排序
    all_results.sort(key=lambda x: x['question_id'])
    
    # 如果指定了输出文件，保存为 JSON
    if output_file:
        output_file = Path(output_file)
        
        # 生成两种格式
        # 格式1: 数组格式（详细）
        detailed_data = []
        for result in all_results:
            detailed_data.append({
                'question_id': result['question_id'],
                'SQL': result['best_sql'] or '',
                'num_paths': result['num_paths']
            })
        
        # 格式2: 字典格式（简洁，question_id 作为 key）
        simple_data = {}
        for result in all_results:
            simple_data[str(result['question_id'])] = result['best_sql'] or ''
        
        # 保存详细格式
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(detailed_data, f, ensure_ascii=False, indent=2)
        print(f"\n✓ 结果已保存到: {output_file}")
        print(f"  共 {len(detailed_data)} 条记录")
        
        # 如果输出文件名包含 "pred_sqls"，也保存简洁格式
        if "pred_sqls" in output_file.name:
            simple_output = output_file.parent / output_file.name.replace(".json", "_simple.json")
            with open(simple_output, 'w', encoding='utf-8') as f:
                json.dump(simple_data, f, ensure_ascii=False, indent=2)
            print(f"✓ 简洁格式已保存到: {simple_output}")
    
    return all_results


def main():
    parser = argparse.ArgumentParser(description="从 Alpha-SQL 运行结果中提取 SQL")
    parser.add_argument("--pkl_file", type=str, help="单个 .pkl 文件路径")
    parser.add_argument("--results_dir", type=str, help="结果目录路径（包含多个 .pkl 文件）")
    parser.add_argument("--output", type=str, help="输出 JSON 文件路径（可选）")
    args = parser.parse_args()
    
    if args.pkl_file:
        # 提取单个文件
        pkl_path = Path(args.pkl_file)
        if not pkl_path.exists():
            print(f"❌ 文件不存在: {pkl_path}")
            return
        
        result = extract_sql_from_pkl_file(pkl_path)
        print(f"\n问题 ID: {result['question_id']}")
        print(f"推理路径数: {result['num_paths']}")
        print(f"\n最佳 SQL:")
        print(result['best_sql'] or "未找到 SQL")
        
        print(f"\n所有路径的 SQL:")
        for path_result in result['all_paths']:
            if path_result['sql']:
                print(f"  路径 {path_result['path_index']}: {path_result['sql']}")
        
        if args.output:
            output_path = Path(args.output)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"\n✓ 结果已保存到: {output_path}")
    
    elif args.results_dir:
        # 提取整个目录
        results_dir = Path(args.results_dir)
        if not results_dir.exists():
            print(f"❌ 目录不存在: {results_dir}")
            return
        
        output_path = Path(args.output) if args.output else None
        all_results = extract_sql_from_directory(results_dir, output_path)
        
        # 统计信息
        total = len(all_results)
        with_sql = sum(1 for r in all_results if r['best_sql'])
        print(f"\n========== 统计信息 ==========")
        print(f"总任务数: {total}")
        print(f"成功提取 SQL: {with_sql}")
        print(f"未找到 SQL: {total - with_sql}")
        print(f"成功率: {with_sql/total*100:.1f}%")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

