#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比较两个JSON文件前30个条目的准确率
"""

import json
import sys
from pathlib import Path


def compare_predicted_res(res1, res2):
    """比较两个predicted_res是否相同"""
    # 转换为集合进行比较，忽略顺序
    try:
        set1 = set(map(tuple, res1)) if res1 else set()
        set2 = set(map(tuple, res2)) if res2 else set()
        return set1 == set2
    except Exception:
        # 如果转换失败，直接比较
        return res1 == res2


def recalculate_success(predicted_res, ground_truth_res):
    """根据predicted_res和ground_truth_res重新计算success"""
    try:
        pred_set = set(map(tuple, predicted_res)) if predicted_res else set()
        truth_set = set(map(tuple, ground_truth_res)) if ground_truth_res else set()
        return 1 if pred_set == truth_set else 0
    except Exception:
        return 0


def load_json_file(file_path):
    """加载JSON文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误: 文件不存在 - {file_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"错误: JSON解析失败 - {file_path}: {e}")
        sys.exit(1)


def calculate_accuracy_first_n(data, n=30):
    """计算前n个条目的准确率"""
    if 'error_details' not in data:
        print("错误: 文件中没有找到 'error_details' 字段")
        return None, None
    
    error_details = data['error_details']
    
    # 取前n个条目
    first_n = error_details[:n]
    
    if len(first_n) == 0:
        print(f"警告: 文件中没有足够的条目（需要至少1个，实际有{len(error_details)}个）")
        return None, None
    
    # 统计success=1的数量
    success_count = sum(1 for item in first_n if item.get('success') == 1)
    total_count = len(first_n)
    accuracy = (success_count / total_count) * 100 if total_count > 0 else 0
    
    return accuracy, {
        'success_count': success_count,
        'total_count': total_count,
        'failed_count': total_count - success_count
    }

def merge_and_recalculate(file1_path, file2_path, n=30):
    """合并两个文件：如果predicted_res相同则使用，否则使用文件2的值，然后重新计算准确率"""
    print(f"正在加载文件...")
    print(f"文件1: {file1_path}")
    print(f"文件2: {file2_path}")
    print(f"处理前 {n} 个条目\n")
    
    # 加载文件
    data1 = load_json_file(file1_path)
    data2 = load_json_file(file2_path)
    
    if 'error_details' not in data1 or 'error_details' not in data2:
        print("错误: 文件中没有找到 'error_details' 字段")
        return
    
    details1 = data1['error_details'][:n]
    details2 = data2['error_details'][:n]
    
    # 创建索引映射
    idx_map1 = {item.get('idx'): item for item in details1}
    idx_map2 = {item.get('idx'): item for item in details2}
    
    # 合并数据
    merged_details = []
    same_count = 0
    different_count = 0
    
    for idx in sorted(idx_map1.keys(), key=lambda x: int(x) if x.isdigit() else 0):
        if idx not in idx_map2:
            continue
            
        item1 = idx_map1[idx]
        item2 = idx_map2[idx]
        
        pred_res1 = item1.get('predicted_res', [])
        pred_res2 = item2.get('predicted_res', [])
        ground_truth_res = item2.get('ground_truth_res', [])
        
        # 比较predicted_res
        if compare_predicted_res(pred_res1, pred_res2):
            # 如果相同，使用这个值
            merged_item = item2.copy()
            merged_item['predicted_res'] = pred_res2
            same_count += 1
        else:
            # 如果不同，使用文件2的值
            merged_item = item2.copy()
            merged_item['predicted_res'] = pred_res2
            different_count += 1
        
        # 重新计算success
        merged_item['success'] = recalculate_success(
            merged_item['predicted_res'],
            ground_truth_res
        )
        
        merged_details.append(merged_item)
    
    # 计算合并后的准确率
    success_count = sum(1 for item in merged_details if item.get('success') == 1)
    total_count = len(merged_details)
    accuracy = (success_count / total_count) * 100 if total_count > 0 else 0
    
    # 输出结果
    print("=" * 80)
    print("合并结果统计")
    print("=" * 80)
    print(f"\npredicted_res相同的条目数: {same_count}")
    print(f"predicted_res不同的条目数: {different_count}")
    print(f"总条目数: {total_count}")
    
    print("\n" + "=" * 80)
    print("合并后的准确率")
    print("=" * 80)
    print(f"  成功数: {success_count}")
    print(f"  失败数: {total_count - success_count}")
    print(f"  总数: {total_count}")
    print(f"  准确率: {accuracy:.2f}%")
    
    return accuracy, {
        'success_count': success_count,
        'total_count': total_count,
        'failed_count': total_count - success_count,
        'same_count': same_count,
        'different_count': different_count
    }
def compare_accuracy(file1_path, file2_path, n=30):
    """比较两个文件前n个条目的准确率"""
    print(f"正在加载文件...")
    print(f"文件1: {file1_path}")
    print(f"文件2: {file2_path}")
    print(f"比较前 {n} 个条目\n")
    
    # 加载文件
    data1 = load_json_file(file1_path)
    data2 = load_json_file(file2_path)
    
    # 计算准确率
    acc1, stats1 = calculate_accuracy_first_n(data1, n)
    acc2, stats2 = calculate_accuracy_first_n(data2, n)
    
    if acc1 is None or acc2 is None:
        return
    
    # 输出结果
    print("=" * 80)
    print("准确率比较结果")
    print("=" * 80)
    print(f"\n文件1: {Path(file1_path).name}")
    print(f"  成功数: {stats1['success_count']}")
    print(f"  失败数: {stats1['failed_count']}")
    print(f"  总数: {stats1['total_count']}")
    print(f"  准确率: {acc1:.2f}%")
    
    print(f"\n文件2: {Path(file2_path).name}")
    print(f"  成功数: {stats2['success_count']}")
    print(f"  失败数: {stats2['failed_count']}")
    print(f"  总数: {stats2['total_count']}")
    print(f"  准确率: {acc2:.2f}%")
    
    print("\n" + "=" * 80)
    print("差异分析")
    print("=" * 80)
    diff = acc2 - acc1
    print(f"准确率差异: {diff:+.2f}% ({'文件2更高' if diff > 0 else '文件1更高' if diff < 0 else '相同'})")
    
    # 详细比较每个条目的success状态
    details1 = data1['error_details'][:n]
    details2 = data2['error_details'][:n]
    
    # 创建索引映射
    idx_map1 = {item.get('idx'): item.get('success', 0) for item in details1}
    idx_map2 = {item.get('idx'): item.get('success', 0) for item in details2}
    
    # 找出差异
    different_items = []
    for idx in idx_map1:
        if idx in idx_map2:
            if idx_map1[idx] != idx_map2[idx]:
                different_items.append({
                    'idx': idx,
                    'file1_success': idx_map1[idx],
                    'file2_success': idx_map2[idx]
                })
    
    if different_items:
        print(f"\n发现 {len(different_items)} 个条目的结果不一致:")
        for item in different_items:  # 只显示前10个
            status1 = "成功" if item['file1_success'] == 1 else "失败"
            status2 = "成功" if item['file2_success'] == 1 else "失败"
            print(f"  索引 {item['idx']}: 文件1={status1}, 文件2={status2}")
        # if len(different_items) > 10:
        #     print(f"  ... 还有 {len(different_items) - 10} 个差异条目")
    else:
        print("\n所有条目的结果都一致")


def find_regression_cases(new_file, old_file1, old_file2, n=None):
    """找出在新文件中失败但在两个旧文件中都成功的案例（回归案例）"""
    print(f"正在查找回归案例...")
    print(f"新文件（有错误）: {new_file}")
    print(f"旧文件1（应该正确）: {old_file1}")
    print(f"旧文件2（应该正确）: {old_file2}")
    if n:
        print(f"分析前 {n} 个条目\n")
    else:
        print(f"分析所有条目\n")
    
    # 加载文件
    data_new = load_json_file(new_file)
    data_old1 = load_json_file(old_file1)
    data_old2 = load_json_file(old_file2)
    
    if 'error_details' not in data_new or 'error_details' not in data_old1 or 'error_details' not in data_old2:
        print("错误: 文件中没有找到 'error_details' 字段")
        return
    
    # 获取数据
    details_new = data_new['error_details'][:n] if n else data_new['error_details']
    details_old1 = data_old1['error_details'][:n] if n else data_old1['error_details']
    details_old2 = data_old2['error_details'][:n] if n else data_old2['error_details']
    
    # 创建索引映射
    idx_map_new = {item.get('idx'): item for item in details_new}
    idx_map_old1 = {item.get('idx'): item for item in details_old1}
    idx_map_old2 = {item.get('idx'): item for item in details_old2}
    
    # 找出回归案例：新文件中失败，但两个旧文件中都成功
    regression_cases = []
    
    for idx in idx_map_new:
        if idx not in idx_map_old1 or idx not in idx_map_old2:
            continue
        
        success_new = idx_map_new[idx].get('success', 0)
        success_old1 = idx_map_old1[idx].get('success', 0)
        success_old2 = idx_map_old2[idx].get('success', 0)
        
        # 新文件失败，但两个旧文件都成功
        if success_new == 0 and success_old1 == 1 and success_old2 == 1:
            regression_cases.append({
                'idx': idx,
                'question': idx_map_new[idx].get('question', ''),
                'predicted_res': idx_map_new[idx].get('predicted_res', []),
                'ground_truth_res': idx_map_new[idx].get('ground_truth_res', []),
                'predicted_sql': idx_map_new[idx].get('predicted_sql', ''),
                'ground_truth_sql': idx_map_new[idx].get('ground_truth_sql', ''),
            })
    
    # 输出结果
    print("=" * 80)
    print("回归案例分析结果")
    print("=" * 80)
    
    # 统计各个文件的准确率
    success_new = sum(1 for item in details_new if item.get('success') == 1)
    success_old1 = sum(1 for item in details_old1 if item.get('success') == 1)
    success_old2 = sum(1 for item in details_old2 if item.get('success') == 1)
    
    total = len(details_new)
    
    print(f"\n各文件准确率统计（共 {total} 个条目）:")
    print(f"  新文件: {success_new}/{total} = {(success_new/total*100):.2f}%")
    print(f"  旧文件1: {success_old1}/{len(details_old1)} = {(success_old1/len(details_old1)*100):.2f}%")
    print(f"  旧文件2: {success_old2}/{len(details_old2)} = {(success_old2/len(details_old2)*100):.2f}%")
    
    print(f"\n找到 {len(regression_cases)} 个回归案例（新文件错误，但两个旧文件都正确）:")
    print("=" * 80)
    
    if regression_cases:
        for i, case in enumerate(regression_cases, 1):
            print(f"\n回归案例 #{i}")
            print(f"索引: {case['idx']}")
            print(f"问题: {case['question'][:100]}...")
            print(f"预测结果: {case['predicted_res'][:3] if len(case['predicted_res']) > 3 else case['predicted_res']}...")
            print(f"正确结果: {case['ground_truth_res'][:3] if len(case['ground_truth_res']) > 3 else case['ground_truth_res']}...")
            print(f"预测SQL: {case['predicted_sql'][:150]}...")
            print("-" * 80)
        
        # 保存到文件
        output_file = "/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts_v1/test/out/regression_cases.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(regression_cases, f, ensure_ascii=False, indent=2)
        print(f"\n回归案例已保存到: {output_file}")
    else:
        print("\n没有找到回归案例！")
    
    return regression_cases


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("用法: ")
        print("  1. 比较两个文件:")
        print("     python compare_accuracy.py <文件1路径> <文件2路径> [前n个条目，默认30]")
        print("\n  2. 查找回归案例（3个文件）:")
        print("     python compare_accuracy.py <新文件（有错）> <旧文件1（正确）> <旧文件2（正确）> [前n个条目，可选]")
        print("\n示例:")
        print("  python compare_accuracy.py /path/to/12_14_closenarrow_stats_acc.json /path/to/12_11_acc.json 70")
        print("  python compare_accuracy.py /path/to/12_16_w_schemaFilter_acc.json /path/to/12_11_acc.json /path/to/12_10_acc.json")
        sys.exit(1)
    
    # 检查是否是回归分析模式（3个或4个参数）
    if len(sys.argv) >= 4 and sys.argv[3].endswith('.json'):
        # 回归分析模式：查找在新文件中错误但在两个旧文件中都正确的案例
        new_file = sys.argv[1]
        old_file1 = sys.argv[2]
        old_file2 = sys.argv[3]
        n = int(sys.argv[4]) if len(sys.argv) > 4 else None
        
        find_regression_cases(new_file, old_file1, old_file2, n)
    else:
        # 原有的两文件比较模式
        file1_path = sys.argv[1]
        file2_path = sys.argv[2]
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        
        # 使用合并逻辑：如果predicted_res相同则使用，否则使用文件2的值
        merge_and_recalculate(file1_path, file2_path, n)
