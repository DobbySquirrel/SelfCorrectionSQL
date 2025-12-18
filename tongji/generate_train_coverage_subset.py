import sys
import os
import json
import re
from collections import Counter, defaultdict
from typing import Dict, List, Set
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np
from sklearn.metrics import silhouette_score # 导入轮廓系数评估方法

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入 node_to_func
try:
    from utils.auto_stat_atomic_operators import node_to_func
except ImportError:
    print("错误: 无法导入 utils/auto_stat_atomic_operators.py。请确保文件存在且路径正确。")
    node_to_func = {} 

def extract_operators_from_operations(operations: List[str]) -> List[str]:
    """从operations列表中提取操作符名称"""
    operator_names = []
    for op_str in operations:
        match = re.search(r'operation:\s*(\w+)', op_str)
        if match:
            operator_name = match.group(1).strip()
            if operator_name and operator_name != "None":
                operator_names.append(operator_name)
    return operator_names

def analyze_train_dataset_for_operator_coverage(filepath: str) -> List[Dict]:
    """分析Train数据集，返回包含操作符信息的SQL条目"""
    print(f"正在分析Train数据集: {filepath}...")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {filepath}")
        return []
    except json.JSONDecodeError as e:
        print(f"错误: JSON解析失败 {e}")
        return []
    
    sql_with_operators = []
    total_sql = len(data)
    
    for i, item in enumerate(data):
        if i % 1000 == 0:
            print(f"     处理进度: {i}/{total_sql}")

        operations = item.get('operations', [])
        if operations:
            operator_names = extract_operators_from_operations(operations)
            unique_operators = list(set(operator_names))
            
            if unique_operators:
                item_with_operators = item.copy()
                item_with_operators['operators'] = unique_operators
                item_with_operators['operator_count'] = len(unique_operators)
                sql_with_operators.append(item_with_operators)
    
    print(f"     Train数据集分析完成，共处理 {total_sql} 条SQL，有效 {len(sql_with_operators)} 条")
    return sql_with_operators

def find_optimal_k(tfidf_matrix: np.ndarray, min_k=30,max_k: int = 45) -> int:
    """
    使用轮廓系数法寻找最优的K值。
    Args:
        tfidf_matrix: TF-IDF 特征矩阵。
        max_k: 尝试的最大K值。
    Returns:
        最优K值。
    """
    if tfidf_matrix.shape[0] < 2: # 至少需要2个样本才能计算轮廓系数
        print("警告: 样本数太少，无法计算轮廓系数来寻找最优K值。将使用默认K=3。")
        return 3 # 默认一个小的K值
    
    # K值至少从2开始（KMeans要求至少两个聚类）
    # K值不能超过样本数减1
    possible_k_values = 40
    
    if not possible_k_values:
        print("警告: 没有可用的K值范围，将使用默认K=3。")
        return 3

    best_k = 2 # 初始最佳K值
    max_silhouette_avg = -1 # 初始最大轮廓系数

    print(f"\n正在寻找最优聚类数 (K值，范围: {min(possible_k_values)} 到 {max(possible_k_values)})...")
    for k in possible_k_values:
        try:
            kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
            cluster_labels = kmeans.fit_predict(tfidf_matrix)
            
            # 只有当一个聚类有多个样本时才能计算轮廓系数，或确保有多个聚类
            if len(set(cluster_labels)) > 1: # 确保至少有两个聚类才能计算
                silhouette_avg = silhouette_score(tfidf_matrix, cluster_labels)
                # print(f"  K={k}, 轮廓系数: {silhouette_avg:.4f}")
                if silhouette_avg > max_silhouette_avg:
                    max_silhouette_avg = silhouette_avg
                    best_k = k
            else:
                 # print(f"  K={k}, 无法计算轮廓系数 (只有一个聚类或样本太少)。")
                 pass # 无法计算时跳过

        except Exception as e:
            print(f"  K={k} 聚类或轮廓系数计算失败: {e}")
            continue # 继续尝试下一个K值

    print(f"最优K值 (基于轮廓系数) 为: {best_k} (轮廓系数: {max_silhouette_avg:.4f})")
    return best_k


def cluster_operator_combinations(data: List[Dict], max_k: int = 20) -> Dict:
    """
    对操作符组合进行聚类，并自动选择最优K值。
    Args:
        data: 包含操作符信息的SQL数据。
        max_k: 寻找最优K值时尝试的最大聚类数量。
    Returns:
        聚类结果字典。
    """
    
    operator_texts = []
    for item in data:
        operators = item.get('operators', [])
        operator_text = ' '.join(sorted(operators))
        operator_texts.append(operator_text)
    
    if not operator_texts:
        print("没有可用于聚类的操作符文本，无法进行聚类。")
        return None

    vectorizer = TfidfVectorizer(max_features=100, stop_words=None)
    try:
        tfidf_matrix = vectorizer.fit_transform(operator_texts)
        
        # 如果数据量太小，无法进行有意义的聚类或计算轮廓系数
        if tfidf_matrix.shape[0] < 2:
            print("数据量太小，无法进行有效聚类。")
            return None

        # 寻找最优K值
        optimal_k = find_optimal_k(tfidf_matrix, max_k=max_k)
        
        print(f"\n最终聚类数选定为: {optimal_k}")
        
        # 使用最优K值进行最终聚类
        kmeans = KMeans(n_clusters=optimal_k, random_state=42, n_init=10)
        cluster_labels = kmeans.fit_predict(tfidf_matrix)
        
        cluster_stats = defaultdict(lambda: {'count': 0, 'items': [], 'operators': set()})
        for i, label in enumerate(cluster_labels):
            cluster_stats[label]['count'] += 1
            cluster_stats[label]['items'].append(data[i])
            cluster_stats[label]['operators'].update(data[i]['operators'])
        
        print(f"聚类完成，各聚类大小:")
        for cluster_id, stats in cluster_stats.items():
            print(f"   聚类 {cluster_id}: {stats['count']} 条SQL, {len(stats['operators'])} 种操作符")
        
        return {
            'cluster_labels': cluster_labels,
            'cluster_stats': dict(cluster_stats),
            'vectorizer': vectorizer,
            'kmeans': kmeans,
            'optimal_k': optimal_k # 添加最优K值到结果中
        }
        
    except Exception as e:
        print(f"聚类失败: {e}")
        return None

def generate_operator_coverage_subset(data: List[Dict]) -> List[Dict]:
    """生成包含所有操作符至少出现一次的子集"""
    print("\n生成操作符覆盖子集 (coverage_small_subset.json)...")
    
    all_known_operators = set(node_to_func.keys())
    if not all_known_operators:
        print("警告: node_to_func为空或导入失败，将从数据中动态识别所有操作符。")
        for item in data:
            all_known_operators.update(item.get('operators', []))
    
    print(f"需要覆盖的操作符数量: {len(all_known_operators)}")
    print(f"所有目标操作符: {sorted(all_known_operators)}")
    
    selected_items = []
    covered_operators = set()
    
    data_sorted = sorted(data, key=lambda x: x.get('operator_count', 0), reverse=True)
    
    for item in data_sorted:
        item_operators = set(item.get('operators', []))
        new_operators = (item_operators & all_known_operators) - covered_operators
        
        if new_operators:
            original_item = {k: v for k, v in item.items() if k not in ['operators', 'operator_count']}
            selected_items.append(original_item)
            covered_operators.update(new_operators)
            
            if len(covered_operators) == len(all_known_operators) and all_known_operators:
                print("所有目标操作符都已覆盖！")
                break
    
    uncovered_operators = all_known_operators - covered_operators
    if uncovered_operators:
        print(f"\n警告: 以下目标操作符未被覆盖 ({len(uncovered_operators)} 个): {list(uncovered_operators)}")
    else:
        print(f"\n成功覆盖所有 {len(all_known_operators)} 个目标操作符！")
    
    print(f"操作符覆盖子集大小: {len(selected_items)} 条SQL")
    print(f"实际覆盖操作符数: {len(covered_operators)} / {len(all_known_operators)}")
    
    return selected_items

def generate_cluster_based_subset(data: List[Dict], cluster_result: Dict, coverage_ratio: float = 0.8) -> List[Dict]:
    """基于聚类结果生成子集，确保每个聚类都有代表性样本"""
    print(f"\n生成基于聚类的子集 (cluster_based_subset.json)，覆盖率: {coverage_ratio}")
    
    if not cluster_result:
        print("没有聚类结果，无法生成基于聚类的子集。")
        return []

    selected_items = []
    cluster_stats = cluster_result['cluster_stats']
    
    for cluster_id, stats in cluster_stats.items():
        items_in_cluster = stats['items']
        select_count = max(1, int(len(items_in_cluster) * coverage_ratio)) if items_in_cluster else 0
        
        items_in_cluster.sort(key=lambda x: x.get('operator_count', 0), reverse=True)
        selected_from_cluster = items_in_cluster[:select_count]
        
        for item in selected_from_cluster:
            original_item = {k: v for k, v in item.items() if k not in ['operators', 'operator_count']}
            selected_items.append(original_item)
        
        print(f"聚类 {cluster_id}: 选择 {select_count}/{len(items_in_cluster)} 条SQL")
    
    print(f"基于聚类的子集大小: {len(selected_items)} 条SQL")
    return selected_items

def main():
    """主函数"""
    print("=== 开始生成操作符覆盖子集和基于聚类的子集 ===")
    
    # Train数据集文件路径
    # 请根据你的实际文件路径修改这里
    train_file = '/home/shenshuyu/SQL_tool/work/bird/train/train_with_operations.json'
    
    # 分析Train数据集
    train_data = analyze_train_dataset_for_operator_coverage(train_file)
    
    if not train_data:
        print("没有找到有效数据，退出")
        return
    
    # 统计操作符分布
    all_operators_in_data = set()
    operator_frequency = Counter()
    
    for item in train_data:
        operators = item.get('operators', [])
        all_operators_in_data.update(operators)
        for op in operators:
            operator_frequency[op] += 1
    
    print(f"\n=== 操作符统计 (基于完整数据集) ===")
    print(f"数据中出现的操作符数量: {len(all_operators_in_data)}")
    print(f"操作符频率分布 (前20个):")
    for op, freq in operator_frequency.most_common(20):
        print(f"   {op}: {freq} 次")
    
    # 对操作符组合进行聚类 (现在会包含最优K值选择)
    # 你可以通过修改这里的 max_k 参数来调整尝试的最大聚类数量
    cluster_result = cluster_operator_combinations(train_data, max_k=30) # 尝试K值到30
    
    # 生成操作符覆盖子集
    coverage_subset = generate_operator_coverage_subset(train_data)
    
    # 生成基于聚类的子集
    cluster_subset = generate_cluster_based_subset(train_data, cluster_result) if cluster_result else []
    
    # 保存结果
    output_dir = 'tongji'
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存操作符覆盖子集
    coverage_output_file = os.path.join(output_dir, 'coverage_small_subset.json')
    try:
        with open(coverage_output_file, 'w', encoding='utf-8') as f:
            json.dump(coverage_subset, f, ensure_ascii=False, indent=2)
        print(f"\n操作符覆盖子集已保存到: {coverage_output_file}")
    except Exception as e:
        print(f"保存覆盖子集失败: {e}")
    
    # 保存聚类子集
    if cluster_subset:
        cluster_output_file = os.path.join(output_dir, 'cluster_based_subset.json')
        try:
            with open(cluster_output_file, 'w', encoding='utf-8') as f:
                json.dump(cluster_subset, f, ensure_ascii=False, indent=2)
            print(f"聚类子集已保存到: {cluster_output_file}")
        except Exception as e:
            print(f"保存聚类子集失败: {e}")
    
    # 生成详细统计报告
    print(f"\n=== 详细统计报告 ===")
    print(f"原始Train数据集大小: {len(train_data)} 条SQL")
    print(f"操作符覆盖子集大小: {len(coverage_subset)} 条SQL")
    print(f"聚类子集大小: {len(cluster_subset)} 条SQL") 
    if cluster_result and 'optimal_k' in cluster_result:
        print(f"聚类使用的最优K值: {cluster_result['optimal_k']}")

    # 再次分析覆盖子集的操作符分布，确保准确性
    coverage_operators_in_subset = set()
    for item in coverage_subset:
        operations = item.get('operations', [])
        if operations:
            operator_names = extract_operators_from_operations(operations)
            coverage_operators_in_subset.update(operator_names)
    
    print(f"\n覆盖子集 (coverage_small_subset.json) 实际包含的独立操作符数量: {len(coverage_operators_in_subset)}")
    print(f"覆盖子集实际操作符: {sorted(coverage_operators_in_subset)}")
    
    # 覆盖子集中SQL操作符数量的分布
    operator_count_dist_coverage = Counter()
    for item in coverage_subset:
        operations = item.get('operations', [])
        if operations:
            operator_names = extract_operators_from_operations(operations)
            unique_operators = list(set(operator_names))
            operator_count_dist_coverage[len(unique_operators)] += 1
    
    print(f"\n覆盖子集 (coverage_small_subset.json) 中SQL的操作符数量分布:")
    for count, num_sql in sorted(operator_count_dist_coverage.items()):
        print(f"   {count} 个操作符: {num_sql} 条SQL")

    # 分析聚类子集的操作符分布（如果它已生成）
    if cluster_subset:
        cluster_operators_in_subset = set()
        for item in cluster_subset:
            operations = item.get('operations', [])
            if operations:
                operator_names = extract_operators_from_operations(operations)
                cluster_operators_in_subset.update(operator_names)
        
        print(f"\n聚类子集 (cluster_based_subset.json) 实际包含的独立操作符数量: {len(cluster_operators_in_subset)}")
        print(f"聚类子集实际操作符: {sorted(cluster_operators_in_subset)}")

        # 聚类子集中SQL操作符数量的分布
        operator_count_dist_cluster = Counter()
        for item in cluster_subset:
            operations = item.get('operations', [])
            if operations:
                operator_names = extract_operators_from_operations(operations)
                unique_operators = list(set(operator_names))
                operator_count_dist_cluster[len(unique_operators)] += 1
        
        print(f"\n聚类子集 (cluster_based_subset.json) 中SQL的操作符数量分布:")
        for count, num_sql in sorted(operator_count_dist_cluster.items()):
            print(f"   {count} 个操作符: {num_sql} 条SQL")

if __name__ == "__main__":
    main()