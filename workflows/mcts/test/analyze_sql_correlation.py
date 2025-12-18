"""
MCTS SQL相关性分析脚本

分析MCTS树中所有SQL的属性与正确性的相关性，包括：
- CTE路径上每个CTE的immediate_score, confidence, bucket_count
- SQL本身的reward和bucket_count
- 与gold answer的对比结果（is_correct）

用于优化MCTS参数和策略。
"""

import json
import pandas as pd
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional
import argparse
from collections import defaultdict
import sys
import os

# 添加项目根目录到路径，以便导入模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))
from core.database_connector import DatabaseConnector


def load_results(json_file: str) -> Dict[str, Any]:
    """加载测试结果JSON文件"""
    with open(json_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_gold_sqls(gold_file: str) -> Dict[str, str]:
    """
    加载gold SQL文件，返回 {question_id: gold_sql} 的字典
    
    支持两种格式：
    1. JSON格式：包含 question_id 和 SQL 字段的列表
    2. SQL文件格式：从 ground_truth_path + data_mode 加载
    """
    gold_sqls = {}
    try:
        # 尝试作为JSON文件加载
        with open(gold_file, 'r', encoding='utf-8') as f:
            gold_data = json.load(f)
        
        # 如果是列表格式
        if isinstance(gold_data, list):
            for item in gold_data:
                qid = item.get('question_id')
                sql = item.get('ground_truth', '') or item.get('sql', '')
                if qid is not None and sql:
                    gold_sqls[str(qid)] = sql
        # 如果是字典格式
        elif isinstance(gold_data, dict):
            for qid, item in gold_data.items():
                if isinstance(item, dict):
                    sql = item.get('ground_truth', '') or item.get('sql', '')
                    if sql:
                        gold_sqls[str(qid)] = sql
                elif isinstance(item, str):
                    gold_sqls[str(qid)] = item
        
        print(f"[Gold] 从 {gold_file} 加载了 {len(gold_sqls)} 条gold SQL")
    except json.JSONDecodeError:
        # 如果不是JSON，尝试作为SQL文件加载
        print(f"[Gold] 尝试作为SQL文件加载: {gold_file}")
        # 这里可以添加SQL文件解析逻辑，如果需要的话
        print(f"[警告] 暂不支持SQL文件格式，请使用JSON格式")
    except Exception as e:
        print(f"[警告] 加载gold文件失败: {e}")
    return gold_sqls


def load_gold_sqls_from_sql_file(ground_truth_path: str, data_mode: str) -> Dict[str, str]:
    """
    从SQL文件中加载gold SQL（参考 compute_intersection.py 的方法）
    
    Args:
        ground_truth_path: ground truth文件所在目录
        data_mode: 数据模式文件名（如 dev_gold_error.sql）
    
    Returns:
        {question_id: gold_sql} 的字典
    """
    gold_sqls = {}
    sql_file_path = os.path.join(ground_truth_path, data_mode)
    
    if not os.path.exists(sql_file_path):
        print(f"[警告] Gold SQL文件不存在: {sql_file_path}")
        return gold_sqls
    
    try:
        with open(sql_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析SQL文件（假设格式为：-- question_id: xxx\nSQL语句）
        # 这里需要根据实际文件格式调整
        lines = content.split('\n')
        current_qid = None
        current_sql = []
        
        for line in lines:
            if line.strip().startswith('--') and 'question_id' in line:
                # 保存之前的SQL
                if current_qid and current_sql:
                    gold_sqls[str(current_qid)] = '\n'.join(current_sql).strip()
                
                # 提取新的question_id
                try:
                    current_qid = line.split('question_id:')[1].strip().split()[0]
                    current_sql = []
                except:
                    current_qid = None
                    current_sql = []
            elif current_qid and line.strip():
                current_sql.append(line)
        
        # 保存最后一个SQL
        if current_qid and current_sql:
            gold_sqls[str(current_qid)] = '\n'.join(current_sql).strip()
        
        print(f"[Gold] 从SQL文件加载了 {len(gold_sqls)} 条gold SQL")
    except Exception as e:
        print(f"[警告] 加载SQL文件失败: {e}")
    
    return gold_sqls


def build_db_connector(db_name: str, db_root_path: Optional[str] = None) -> Optional[DatabaseConnector]:
    """
    构建数据库连接器
    
    Args:
        db_name: 数据库名称
        db_root_path: 数据库根路径（可选，默认使用标准路径）
    
    Returns:
        DatabaseConnector 实例，如果失败则返回 None
    """
    if db_root_path:
        db_path = os.path.join(db_root_path, db_name, f"{db_name}.sqlite")
    else:
        # 默认路径（参考 test_mcts.py）
        db_path = f"/ssd/shenshuyu/work/bird/dev_20240627/dev_databases/{db_name}/{db_name}.sqlite"
    
    if not os.path.exists(db_path):
        print(f"[警告] 数据库文件不存在: {db_path}")
        return None
    
    try:
        db_connector = DatabaseConnector(db_path)
        if db_connector.connect():
            return db_connector
        else:
            print(f"[警告] 无法连接到数据库: {db_path}")
            return None
    except Exception as e:
        print(f"[警告] 创建数据库连接器失败: {e}")
        return None


def normalize_sql_for_comparison(sql: str) -> str:
    """标准化SQL用于字符串比较"""
    if not sql:
        return ""
    # 移除多余空白、转换为小写、移除分号
    normalized = ' '.join(sql.split()).lower().rstrip(';').strip()
    return normalized


def compare_with_gold(predicted_sql: str, gold_sql: str, db_connector: Optional[DatabaseConnector] = None) -> bool:
    """
    比较预测SQL和gold SQL的执行结果是否相同
    
    Args:
        predicted_sql: 预测的SQL
        gold_sql: 标准答案SQL
        db_connector: 数据库连接器（如果提供，则执行SQL比较结果；否则回退到字符串比较）
    
    Returns:
        bool: 如果结果匹配则为True，否则为False
    """
    # 如果提供了数据库连接器，执行SQL并比较结果
    if db_connector is not None:
        try:
            # 执行gold SQL（返回 (DataFrame, error_message) 元组）
            gold_result, gold_error = db_connector.execute_query(gold_sql)
            if gold_result is None or gold_error:
                # 回退到字符串比较
                pred_norm = normalize_sql_for_comparison(predicted_sql)
                gold_norm = normalize_sql_for_comparison(gold_sql)
                return pred_norm == gold_norm
            
            # 执行predicted SQL（返回 (DataFrame, error_message) 元组）
            predicted_result, predicted_error = db_connector.execute_query(predicted_sql)
            if predicted_result is None or predicted_error:
                return False
            
            # 比较结果（转换为集合进行比较，忽略顺序）
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
            
            # 转换为可比较的格式（处理NaN、None等）
            def normalize_row(row):
                """标准化行数据，处理NaN、None等"""
                normalized = {}
                for k, v in row.items():
                    if pd.isna(v) or v is None:
                        normalized[k] = None
                    elif isinstance(v, (np.integer, np.floating)):
                        normalized[k] = float(v)
                    elif isinstance(v, (int, float)):
                        normalized[k] = float(v) if isinstance(v, float) else int(v)
                    else:
                        normalized[k] = str(v)
                return tuple(sorted(normalized.items()))
            
            gold_set = {normalize_row(row) for row in gold_normalized}
            predicted_set = {normalize_row(row) for row in predicted_normalized}
            
            # 比较集合
            return gold_set == predicted_set
            
        except Exception as e:
            # 出错时回退到字符串比较
            pred_norm = normalize_sql_for_comparison(predicted_sql)
            gold_norm = normalize_sql_for_comparison(gold_sql)
            return pred_norm == gold_norm
    else:
        # 没有数据库连接器，使用字符串比较
        pred_norm = normalize_sql_for_comparison(predicted_sql)
        gold_norm = normalize_sql_for_comparison(gold_sql)
        return pred_norm == gold_norm


def extract_sql_features(all_sqls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    从所有SQL中提取特征
    
    Args:
        all_sqls: 包含所有SQL及其属性的列表
        
    Returns:
        特征列表，每个元素包含：
        - is_correct: 是否正确（True/False/None）
        - reward: SQL的奖励值
        - bucket_count: SQL的桶计数
        - cte1_immediate_score: 第一个CTE的即时评分
        - cte1_confidence: 第一个CTE的信心度
        - cte1_bucket_count: 第一个CTE的桶计数
        - cte2_immediate_score: 第二个CTE的即时评分
        - ... (以此类推)
        - avg_cte_immediate_score: 平均CTE即时评分
        - avg_cte_confidence: 平均CTE信心度
        - avg_cte_bucket_count: 平均CTE桶计数
        - cte_path_length: CTE路径长度
    """
    features = []
    
    for sql_info in all_sqls:
        if sql_info.get('is_correct') is None:
            continue  # 跳过没有gold验证的SQL
        
        feature = {
            'is_correct': sql_info.get('is_correct', False),
            'reward': sql_info.get('reward', 0.0),
            'bucket_count': sql_info.get('bucket_count', 0),
            'visit_count': sql_info.get('visit_count', 0),
            'depth': sql_info.get('depth', 0),
        }
        
        # 提取CTE路径特征
        cte_path = sql_info.get('cte_path', [])
        feature['cte_path_length'] = len(cte_path)
        
        # 提取每个CTE的特征
        cte_immediate_scores = []
        cte_confidences = []
        cte_bucket_counts = []
        
        for i, cte_info in enumerate(cte_path, 1):
            immediate_score = cte_info.get('immediate_score')
            confidence = cte_info.get('confidence')
            bucket_count = cte_info.get('bucket_count', 0)
            
            if immediate_score is not None:
                feature[f'cte{i}_immediate_score'] = immediate_score
                cte_immediate_scores.append(immediate_score)
            
            if confidence is not None:
                feature[f'cte{i}_confidence'] = confidence
                cte_confidences.append(confidence)
            
            feature[f'cte{i}_bucket_count'] = bucket_count
            cte_bucket_counts.append(bucket_count)
        
        # 计算平均值
        if cte_immediate_scores:
            feature['avg_cte_immediate_score'] = np.mean(cte_immediate_scores)
            feature['min_cte_immediate_score'] = np.min(cte_immediate_scores)
            feature['max_cte_immediate_score'] = np.max(cte_immediate_scores)
        
        if cte_confidences:
            feature['avg_cte_confidence'] = np.mean(cte_confidences)
            feature['min_cte_confidence'] = np.min(cte_confidences)
            feature['max_cte_confidence'] = np.max(cte_confidences)
        
        if cte_bucket_counts:
            feature['avg_cte_bucket_count'] = np.mean(cte_bucket_counts)
            feature['total_cte_bucket_count'] = np.sum(cte_bucket_counts)
        
        features.append(feature)
    
    return features


def calculate_correlations(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算各特征与is_correct的相关性
    
    Args:
        df: 包含所有特征的DataFrame
        
    Returns:
        相关性DataFrame，按相关性绝对值排序
    """
    # 只计算数值列与is_correct的相关性
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    numeric_cols = [col for col in numeric_cols if col != 'is_correct']
    
    correlations = []
    for col in numeric_cols:
        corr = df[col].corr(df['is_correct'])
        if not np.isnan(corr):
            correlations.append({
                'feature': col,
                'correlation': corr,
                'abs_correlation': abs(corr)
            })
    
    corr_df = pd.DataFrame(correlations)
    corr_df = corr_df.sort_values('abs_correlation', ascending=False)
    return corr_df


def analyze_by_correctness(df: pd.DataFrame) -> Dict[str, Any]:
    """
    按正确性分组分析特征统计
    
    Args:
        df: 包含所有特征的DataFrame
        
    Returns:
        统计信息字典
    """
    if 'is_correct' not in df.columns:
        return {}
    
    correct_df = df[df['is_correct'] == True]
    incorrect_df = df[df['is_correct'] == False]
    
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    numeric_cols = [col for col in numeric_cols if col != 'is_correct']
    
    stats = {}
    for col in numeric_cols:
        if col in df.columns:
            stats[col] = {
                'correct_mean': correct_df[col].mean() if len(correct_df) > 0 else None,
                'correct_std': correct_df[col].std() if len(correct_df) > 0 else None,
                'incorrect_mean': incorrect_df[col].mean() if len(incorrect_df) > 0 else None,
                'incorrect_std': incorrect_df[col].std() if len(incorrect_df) > 0 else None,
                'correct_count': len(correct_df),
                'incorrect_count': len(incorrect_df),
            }
    
    return stats


def main():
    parser = argparse.ArgumentParser(description="分析MCTS SQL相关性")
    parser.add_argument("--json_file", type=str,default='/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_04_error.json', help="测试结果JSON文件")
    parser.add_argument("--output_dir", type=str, default=None, help="输出目录（默认与JSON文件同目录）")
    parser.add_argument("--gold_file", type=str, default='/home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set_error.json', help="Gold SQL JSON文件路径（可选）")
    parser.add_argument("--ground_truth_path", type=str, default=None, help="Ground truth文件目录（可选，与--data_mode配合使用）")
    parser.add_argument("--data_mode", type=str, default=None, help="数据模式文件名，如 dev_gold_error.sql（可选，与--ground_truth_path配合使用）")
    parser.add_argument("--ppl_file", type=str, default=None, help="原始数据文件路径（用于获取数据库信息，可选）")
    parser.add_argument("--db_root_path", type=str, default='/home/shenshuyu/RSL_SQL/RSL-SQL/database/dev_databases/', help="数据库根路径（可选，默认使用标准路径）")
    args = parser.parse_args()
    
    # 加载结果
    print(f"加载结果文件: {args.json_file}")
    results = load_results(args.json_file)
    print(f"✅ 加载完成，共 {len(results)} 条记录")
    
    # 加载gold SQL（如果提供）
    gold_sqls = {}
    if args.gold_file:
        gold_sqls = load_gold_sqls(args.gold_file)
    elif args.ground_truth_path and args.data_mode:
        gold_sqls = load_gold_sqls_from_sql_file(args.ground_truth_path, args.data_mode)
    
    # 加载原始数据文件以获取数据库信息（如果提供）
    db_info_map = {}  # {question_id: db_name}
    if args.ppl_file:
        try:
            with open(args.ppl_file, 'r', encoding='utf-8') as f:
                ppls = json.load(f)
            for item in ppls:
                qid = str(item.get('question_id', ''))
                db_name = item.get('db', '')
                if qid and db_name:
                    db_info_map[qid] = db_name
            print(f"[数据库信息] 从 {args.ppl_file} 加载了 {len(db_info_map)} 条数据库信息")
        except Exception as e:
            print(f"[警告] 加载原始数据文件失败: {e}")
    
    # 如果提供了gold SQL，对所有SQL进行验证
    # 或者如果stat文件中有gold_match字段，使用它
    use_gold_match = False
    for qid, data in results.items():
        if 'stats' in data and 'gold_match' in data['stats']:
            use_gold_match = True
            break
    
    if use_gold_match:
        print(f"\n使用stat文件中的gold_match标记...")
        total_verified = 0
        correct_count = 0
        
        for qid, data in results.items():
            all_sqls = data.get('all_sqls_with_attributes', [])
            if not all_sqls:
                continue
            
            # 从stats中获取gold_match
            stats = data.get('stats', {})
            gold_match = stats.get('gold_match', None)
            
            if gold_match is None:
                # 如果没有gold_match标记，标记为None
                for sql_info in all_sqls:
                    sql_info['is_correct'] = None
                continue
            
            # 将gold_match应用到所有SQL
            for sql_info in all_sqls:
                sql_info['is_correct'] = gold_match
                total_verified += 1
                if gold_match:
                    correct_count += 1
        
        print(f"✅ 标记完成，共标记 {total_verified} 条SQL（正确: {correct_count}, 错误: {total_verified - correct_count}）")
    elif gold_sqls:
        print(f"\n开始验证 {len(gold_sqls)} 条gold SQL...")
        total_verified = 0
        correct_count = 0
        
        for qid, data in results.items():
            all_sqls = data.get('all_sqls_with_attributes', [])
            if not all_sqls:
                continue
            
            gold_sql = gold_sqls.get(qid)
            if not gold_sql:
                # 如果没有对应的gold SQL，标记为None
                for sql_info in all_sqls:
                    if 'is_correct' not in sql_info:
                        sql_info['is_correct'] = None
                continue
            
            # 获取数据库连接器
            db_connector = None
            if qid in db_info_map:
                db_name = db_info_map[qid]
                db_connector = build_db_connector(db_name, args.db_root_path)
                if db_connector is None:
                    print(f"[Gold验证] ⚠️ 无法创建数据库连接器 (qid={qid}, db={db_name})，将使用字符串比较")
            
            # 验证每个SQL
            for sql_info in all_sqls:
                sql = sql_info.get('sql', '')
                if sql:
                    try:
                        sql_match = compare_with_gold(sql, gold_sql, db_connector=db_connector)
                        sql_info['is_correct'] = sql_match
                        total_verified += 1
                        if sql_match:
                            correct_count += 1
                        # 打印前几个验证结果用于调试
                        if total_verified <= 3:
                            method = "执行结果比较" if db_connector else "字符串比较"
                            status = "✅ 正确" if sql_match else "❌ 错误"
                            print(f"[Gold验证] qid={qid} ({method}): {status}")
                    except Exception as e:
                        print(f"[Gold验证] ⚠️ SQL验证失败 (qid={qid}): {e}")
                        import traceback
                        traceback.print_exc()
                        sql_info['is_correct'] = False
                        total_verified += 1
                else:
                    sql_info['is_correct'] = False
                    total_verified += 1
            
            # 关闭数据库连接
            if db_connector:
                try:
                    db_connector.disconnect()
                except:
                    pass
        
        print(f"✅ 验证完成，共验证 {total_verified} 条SQL（正确: {correct_count}, 错误: {total_verified - correct_count}）")
    else:
        print("⚠️ 未提供gold SQL文件或gold_match标记，跳过验证步骤")
    
    # 提取所有SQL特征（在验证之后）
    all_features = []
    for qid, data in results.items():
        all_sqls = data.get('all_sqls_with_attributes', [])
        if not all_sqls:
            continue
        
        features = extract_sql_features(all_sqls)
        for feat in features:
            feat['question_id'] = qid
        all_features.extend(features)
    
    if not all_features:
        print("⚠️ 没有找到任何SQL数据，请确保JSON文件包含all_sqls_with_attributes字段")
        return
    
    print(f"提取了 {len(all_features)} 条SQL特征")
    
    # 转换为DataFrame
    df = pd.DataFrame(all_features)
    
    # 过滤掉is_correct为None的数据
    df = df[df['is_correct'].notna()]
    
    if len(df) == 0:
        print("⚠️ 没有找到任何有gold验证的SQL数据")
        return
    
    print(f"有效数据: {len(df)} 条（正确: {df['is_correct'].sum()}, 错误: {(~df['is_correct']).sum()}）")
    
    # 计算相关性
    print("\n计算相关性...")
    corr_df = calculate_correlations(df)
    
    # 按正确性分组分析
    print("\n按正确性分组分析...")
    stats = analyze_by_correctness(df)
    
    # 确定输出目录
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path(args.json_file).parent
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存相关性结果
    corr_file = output_dir / "correlation_analysis.csv"
    corr_df.to_csv(corr_file, index=False, encoding='utf-8-sig')
    print(f"\n✅ 相关性分析结果已保存: {corr_file}")
    
    # 保存统计信息
    stats_file = output_dir / "feature_statistics.json"
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"✅ 特征统计信息已保存: {stats_file}")
    
    # 保存完整特征数据
    features_file = output_dir / "all_sql_features.csv"
    df.to_csv(features_file, index=False, encoding='utf-8-sig')
    print(f"✅ 完整特征数据已保存: {features_file}")
    
    # 打印Top相关性
    print("\n" + "="*80)
    print("Top 20 相关性特征（按绝对值排序）:")
    print("="*80)
    for idx, row in corr_df.head(20).iterrows():
        corr = row['correlation']
        direction = "✅" if corr > 0 else "❌"
        print(f"{direction} {row['feature']:40s} : {corr:7.4f}")
    
    # 打印关键统计
    print("\n" + "="*80)
    print("关键特征统计（正确 vs 错误）:")
    print("="*80)
    key_features = ['reward', 'bucket_count', 'avg_cte_immediate_score', 'avg_cte_confidence', 
                   'avg_cte_bucket_count', 'cte_path_length']
    for feat in key_features:
        if feat in stats:
            s = stats[feat]
            if s['correct_mean'] is not None and s['incorrect_mean'] is not None:
                print(f"\n{feat}:")
                print(f"  正确SQL: 均值={s['correct_mean']:.4f}, 标准差={s['correct_std']:.4f}, 数量={s['correct_count']}")
                print(f"  错误SQL: 均值={s['incorrect_mean']:.4f}, 标准差={s['incorrect_std']:.4f}, 数量={s['incorrect_count']}")
                diff = s['correct_mean'] - s['incorrect_mean']
                print(f"  差异: {diff:.4f} ({'✅' if diff > 0 else '❌'})")


if __name__ == "__main__":
    main()


# python workflows/mcts/test/analyze_sql_correlation.py     --json_file /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_04_error.json     --gold_file /home/shenshuyu/SQL_tool_multiAgent/data/subset_ppl_dev_python.json     --ppl_file /home/shenshuyu/SQL_tool_multiAgent/data/subset_ppl_dev_python.json     --db_root_path /home/shenshuyu/RSL_SQL/RSL-SQL/database/dev_databases/
