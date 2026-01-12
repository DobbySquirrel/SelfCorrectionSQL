"""
全面分析所有49个"都没生成对的"案例
找出主要错误模式和原因
"""

import json
import sys
import re
from pathlib import Path
from typing import Dict, List, Set
from collections import defaultdict, Counter

sys.path.append(str(Path(__file__).parent.parent.parent.parent))


def extract_select_fields(sql: str) -> Set[str]:
    """提取SELECT语句中的字段"""
    # 移除注释和多余空格
    sql_clean = re.sub(r'--.*', '', sql)
    sql_clean = re.sub(r'/\*.*?\*/', '', sql_clean, flags=re.DOTALL)
    
    # 查找SELECT ... FROM
    match = re.search(r'SELECT\s+(.*?)\s+FROM', sql_clean, re.IGNORECASE | re.DOTALL)
    if not match:
        return set()
    
    select_part = match.group(1).strip()
    
    # 处理DISTINCT
    if select_part.upper().startswith('DISTINCT'):
        select_part = select_part[8:].strip()
    
    # 分割字段（考虑括号和别名）
    fields = []
    current_field = ""
    paren_count = 0
    
    for char in select_part:
        if char == '(':
            paren_count += 1
            current_field += char
        elif char == ')':
            paren_count -= 1
            current_field += char
        elif char == ',' and paren_count == 0:
            fields.append(current_field.strip())
            current_field = ""
        else:
            current_field += char
    
    if current_field.strip():
        fields.append(current_field.strip())
    
    # 提取字段名（去除别名和函数）
    field_names = set()
    for field in fields:
        field = field.strip()
        # 处理别名 AS xxx
        if ' AS ' in field.upper():
            field = field.split(' AS ')[-1].strip()
        # 处理别名空格
        parts = field.split()
        if len(parts) > 1 and not any(op in parts[0].upper() for op in ['COUNT', 'SUM', 'AVG', 'MAX', 'MIN']):
            field = parts[-1]
        # 去除表别名前缀
        if '.' in field:
            field = field.split('.')[-1]
        # 去除引号
        field = field.strip('`"\'')
        if field:
            field_names.add(field.lower())
    
    return field_names


def extract_where_conditions(sql: str) -> List[str]:
    """提取WHERE条件"""
    match = re.search(r'WHERE\s+(.*?)(?:\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|$)', sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    
    where_part = match.group(1).strip()
    # 简单分割AND/OR条件
    conditions = re.split(r'\s+AND\s+|\s+OR\s+', where_part, flags=re.IGNORECASE)
    return [c.strip() for c in conditions if c.strip()]


def extract_joins(sql: str) -> List[str]:
    """提取JOIN信息"""
    joins = []
    # 查找所有JOIN
    join_pattern = r'(?:INNER\s+)?JOIN\s+(\w+)\s+(?:\w+\s+)?ON\s+([^\s]+)\s*=\s*([^\s]+)'
    matches = re.finditer(join_pattern, sql, re.IGNORECASE)
    for match in matches:
        table = match.group(1)
        left_col = match.group(2)
        right_col = match.group(3)
        joins.append(f"{table}:{left_col}={right_col}")
    return joins


def classify_error_type(gold_sql: str, predicted_sql: str, eval_result: Dict, original_result: Dict = None) -> Dict:
    """
    详细分类错误类型和原因
    
    Returns:
        Dict包含错误类型、详细原因、修复建议等
    """
    error_info = {
        'error_type': '未知错误',
        'error_category': '其他',
        'primary_issue': '',
        'secondary_issues': [],
        'error_details': {},
        'fix_suggestions': []
    }
    
    gold_fields = extract_select_fields(gold_sql)
    pred_fields = extract_select_fields(predicted_sql)
    
    # 1. 检查执行错误（最高优先级）
    # 只检查selected_sql（source为"selected"）的执行错误，不检查其他variants
    matches = eval_result.get('matches', [])
    execution_error = None
    execution_error_type = None
    
    # 找到selected_sql的评估结果
    selected_match = None
    for m in matches:
        if m.get('source') == 'selected':
            selected_match = m
            break
    
    # 只检查selected_sql的错误
    if selected_match and selected_match.get('error'):
        error_msg = selected_match.get('error', '').lower()
        if 'no such column' in error_msg:
            execution_error = selected_match.get('error', '')
            execution_error_type = '列名错误'
            error_info['error_category'] = '语法错误'
            error_info['primary_issue'] = '列名不存在或引用错误'
            # 提取列名
            col_match = re.search(r"no such column:\s*([^\s]+)", error_msg, re.IGNORECASE)
            if col_match:
                error_info['error_details']['missing_column'] = col_match.group(1)
            error_info['fix_suggestions'].append('检查列名拼写和表别名')
        elif 'ambiguous' in error_msg:
            execution_error = selected_match.get('error', '')
            execution_error_type = '列名歧义'
            error_info['error_category'] = '语法错误'
            error_info['primary_issue'] = '列名在多表中存在，需要指定表别名'
            col_match = re.search(r"ambiguous column name:\s*([^\s]+)", error_msg, re.IGNORECASE)
            if col_match:
                error_info['error_details']['ambiguous_column'] = col_match.group(1)
            error_info['fix_suggestions'].append('在列名前添加表别名（如 table.column）')
        elif 'syntax error' in error_msg or 'syntax' in error_msg:
            execution_error = selected_match.get('error', '')
            execution_error_type = '语法错误'
            error_info['error_category'] = '语法错误'
            error_info['primary_issue'] = 'SQL语法错误'
            error_info['fix_suggestions'].append('检查SQL语法，特别是括号、引号、关键字等')
    
    if execution_error:
        error_info['error_type'] = execution_error_type
        error_info['error_details']['execution_error'] = execution_error
        return error_info
    
    # 2. 检查字段问题
    missing_fields = gold_fields - pred_fields
    extra_fields = pred_fields - gold_fields
    
    if missing_fields:
        error_info['error_category'] = '字段问题'
        error_info['error_type'] = f'字段缺失({len(missing_fields)}个)'
        error_info['primary_issue'] = f'缺少{len(missing_fields)}个必需字段'
        error_info['error_details']['missing_fields'] = list(missing_fields)
        error_info['fix_suggestions'].append(f'在SELECT中添加缺失字段: {", ".join(missing_fields)}')
        
        if extra_fields:
            error_info['secondary_issues'].append(f'多余字段({len(extra_fields)}个): {", ".join(extra_fields)}')
        
        return error_info
    
    if extra_fields:
        error_info['error_category'] = '字段问题'
        error_info['error_type'] = f'多余字段({len(extra_fields)}个)'
        error_info['primary_issue'] = f'包含{len(extra_fields)}个不需要的字段'
        error_info['error_details']['extra_fields'] = list(extra_fields)
        error_info['fix_suggestions'].append(f'从SELECT中移除多余字段: {", ".join(extra_fields)}')
        return error_info
    
    # 3. 检查WHERE条件
    gold_conditions = extract_where_conditions(gold_sql)
    pred_conditions = extract_where_conditions(predicted_sql)
    
    if len(gold_conditions) != len(pred_conditions):
        error_info['error_category'] = '条件错误'
        error_info['error_type'] = 'WHERE条件数量不匹配'
        error_info['primary_issue'] = f'Gold有{len(gold_conditions)}个条件，Predicted有{len(pred_conditions)}个'
        error_info['error_details']['gold_conditions_count'] = len(gold_conditions)
        error_info['error_details']['pred_conditions_count'] = len(pred_conditions)
        error_info['fix_suggestions'].append('检查WHERE子句，确保包含所有必需的条件')
        return error_info
    
    # 4. 检查JOIN
    gold_joins = extract_joins(gold_sql)
    pred_joins = extract_joins(predicted_sql)
    
    if len(gold_joins) != len(pred_joins):
        error_info['error_category'] = 'JOIN错误'
        error_info['error_type'] = 'JOIN数量不匹配'
        error_info['primary_issue'] = f'Gold有{len(gold_joins)}个JOIN，Predicted有{len(pred_joins)}个'
        error_info['error_details']['gold_joins_count'] = len(gold_joins)
        error_info['error_details']['pred_joins_count'] = len(pred_joins)
        error_info['fix_suggestions'].append('检查JOIN子句，确保包含所有必需的表连接')
        return error_info
    
    # 5. 检查子查询
    gold_has_subquery = 'SELECT' in gold_sql.upper().replace('SELECT', '', 1)
    pred_has_subquery = 'SELECT' in predicted_sql.upper().replace('SELECT', '', 1)
    
    if gold_has_subquery != pred_has_subquery:
        error_info['error_category'] = '结构错误'
        error_info['error_type'] = '子查询差异'
        error_info['primary_issue'] = 'Gold SQL有子查询但Predicted没有，或反之'
        error_info['error_details']['gold_has_subquery'] = gold_has_subquery
        error_info['error_details']['pred_has_subquery'] = pred_has_subquery
        error_info['fix_suggestions'].append('检查是否需要子查询来实现复杂逻辑')
        return error_info
    
    # 6. 检查聚合函数
    gold_has_agg = any(func in gold_sql.upper() for func in ['COUNT', 'SUM', 'AVG', 'MAX', 'MIN'])
    pred_has_agg = any(func in predicted_sql.upper() for func in ['COUNT', 'SUM', 'AVG', 'MAX', 'MIN'])
    
    if gold_has_agg != pred_has_agg:
        error_info['error_category'] = '聚合错误'
        error_info['error_type'] = '聚合函数差异'
        error_info['primary_issue'] = 'Gold SQL使用聚合函数但Predicted没有，或反之'
        error_info['error_details']['gold_has_agg'] = gold_has_agg
        error_info['error_details']['pred_has_agg'] = pred_has_agg
        error_info['fix_suggestions'].append('检查是否需要聚合函数（COUNT, SUM, AVG等）')
        return error_info
    
    # 7. 检查ORDER BY
    gold_has_order = 'ORDER BY' in gold_sql.upper()
    pred_has_order = 'ORDER BY' in predicted_sql.upper()
    
    if gold_has_order != pred_has_order:
        error_info['error_category'] = '排序错误'
        error_info['error_type'] = 'ORDER BY差异'
        error_info['primary_issue'] = 'Gold SQL有ORDER BY但Predicted没有，或反之'
        error_info['fix_suggestions'].append('检查是否需要ORDER BY排序')
        return error_info
    
    # 8. 检查LIMIT
    gold_has_limit = 'LIMIT' in gold_sql.upper()
    pred_has_limit = 'LIMIT' in predicted_sql.upper()
    
    if gold_has_limit != pred_has_limit:
        error_info['error_category'] = '限制错误'
        error_info['error_type'] = 'LIMIT差异'
        error_info['primary_issue'] = 'Gold SQL有LIMIT但Predicted没有，或反之'
        error_info['fix_suggestions'].append('检查是否需要LIMIT限制结果数量')
        return error_info
    
    # 9. 检查CTE使用
    gold_has_cte = 'WITH' in gold_sql.upper()
    pred_has_cte = 'WITH' in predicted_sql.upper()
    
    if gold_has_cte != pred_has_cte:
        error_info['secondary_issues'].append('CTE使用差异')
    
    # 10. 检查FROM子句的表
    gold_tables = set(re.findall(r'FROM\s+(\w+)', gold_sql, re.IGNORECASE))
    pred_tables = set(re.findall(r'FROM\s+(\w+)', predicted_sql, re.IGNORECASE))
    
    if gold_tables != pred_tables:
        error_info['secondary_issues'].append(f'表差异: Gold使用{gold_tables}, Predicted使用{pred_tables}')
    
    # 默认：逻辑错误
    error_info['error_category'] = '逻辑错误'
    error_info['error_type'] = '逻辑错误'
    error_info['primary_issue'] = 'SQL逻辑不正确，可能涉及条件、计算、表关系等'
    error_info['fix_suggestions'].append('检查SQL的整体逻辑，可能需要重新理解问题需求')
    
    return error_info


def analyze_all_no_match_cases(error_analysis_file: str, result_file: str, eval_file: str):
    """分析所有49个案例"""
    
    print("[加载] 正在加载数据...")
    
    # 加载错误分析结果
    with open(error_analysis_file, 'r', encoding='utf-8') as f:
        error_analysis = json.load(f)
    
    # 加载原始结果
    with open(result_file, 'r', encoding='utf-8') as f:
        result_data = json.load(f)
    
    # 加载评估结果
    with open(eval_file, 'r', encoding='utf-8') as f:
        eval_data = json.load(f)
    
    # 获取所有"都没生成对的"案例
    no_match_cases = error_analysis.get('no_match_generated_analysis', {}).get('cases', [])
    evaluation_results = {r['question_id']: r for r in eval_data.get('evaluation_results', [])}
    
    print(f"[分析] 找到 {len(no_match_cases)} 个案例")
    
    # 分类统计
    error_types = Counter()
    error_details = []
    reward_distribution = defaultdict(int)
    execution_errors = []
    field_missing_cases = []
    logic_error_cases = []
    
    for case in no_match_cases:
        question_id = case['question_id']
        
        # 获取原始数据
        original_result = result_data.get(question_id, {})
        stats = original_result.get('stats', {})
        gold_sql = stats.get('gold_sql', '')
        selected_sql = case.get('selected_sql', '')
        
        # 获取评估结果
        eval_result = evaluation_results.get(question_id, {})
        
        if not gold_sql or not selected_sql:
            continue
        
        # 详细分类错误类型
        error_info = classify_error_type(gold_sql, selected_sql, eval_result, original_result)
        error_type = error_info['error_type']
        error_category = error_info['error_category']
        
        error_types[error_type] += 1
        
        # 统计reward分布
        max_reward = case.get('max_reward', 0.0)
        if max_reward >= 0.9:
            reward_distribution['高(>=0.9)'] += 1
        elif max_reward >= 0.8:
            reward_distribution['中高(0.8-0.9)'] += 1
        elif max_reward >= 0.5:
            reward_distribution['中(0.5-0.8)'] += 1
        else:
            reward_distribution['低(<0.5)'] += 1
        
        # 检查执行错误（从原始结果文件读取）
        rollout_stats = original_result.get('rollout_stats', [])
        selected_sql_valid = False
        selected_sql_error = None
        
        # 查找selected_sql在rollout中的执行状态
        for rollout in rollout_stats:
            all_sql_variants = rollout.get('all_sql_variants', [])
            for sql_var in all_sql_variants:
                if sql_var.get('sql', '').strip() == selected_sql.strip():
                    selected_sql_valid = sql_var.get('valid', False)
                    if not selected_sql_valid:
                        selected_sql_error = sql_var.get('error', None)
                    break
            if selected_sql_error is not None:
                break
        
        # 也检查评估阶段的执行错误（只检查selected_sql，不检查其他variants）
        matches = eval_result.get('matches', [])
        eval_error = None
        # 只检查source为"selected"的SQL的执行错误
        for m in matches:
            if m.get('source') == 'selected' and m.get('error'):
                eval_error = m.get('error', '')
                if 'no such column' in eval_error.lower() or 'ambiguous' in eval_error.lower():
                    execution_errors.append({
                        'question_id': question_id,
                        'error': eval_error,
                        'reward': max_reward,
                        'rollout_valid': selected_sql_valid,
                        'rollout_error': selected_sql_error
                    })
                    break
        
        # 详细分析
        gold_fields = extract_select_fields(gold_sql)
        pred_fields = extract_select_fields(selected_sql)
        missing_fields = gold_fields - pred_fields
        extra_fields = pred_fields - gold_fields
        
        if missing_fields:
            field_missing_cases.append({
                'question_id': question_id,
                'missing_fields': list(missing_fields),
                'extra_fields': list(extra_fields) if extra_fields else [],
                'reward': max_reward,
                'rollout_valid': selected_sql_valid
            })
        
        if error_category == "逻辑错误" or "差异" in error_type:
            logic_error_cases.append({
                'question_id': question_id,
                'error_type': error_type,
                'error_category': error_category,
                'primary_issue': error_info.get('primary_issue', ''),
                'reward': max_reward
            })
        
        error_details.append({
            'question_id': question_id,
            'error_type': error_type,
            'error_category': error_category,
            'primary_issue': error_info.get('primary_issue', ''),
            'secondary_issues': error_info.get('secondary_issues', []),
            'error_details': error_info.get('error_details', {}),
            'fix_suggestions': error_info.get('fix_suggestions', []),
            'max_reward': max_reward,
            'rollout_valid': selected_sql_valid,
            'rollout_error': selected_sql_error,
            'eval_error': eval_error,
            'total_sqls': case.get('total_sqls', 0),
            'total_rollouts': case.get('total_rollouts', 0),
            'valid_count': sum(1 for r in rollout_stats for sv in r.get('all_sql_variants', []) if sv.get('valid', False))
        })
    
    # 按错误类别统计
    error_categories = Counter()
    rollout_valid_count = 0
    rollout_invalid_count = 0
    
    for detail in error_details:
        error_categories[detail.get('error_category', '未知')] += 1
        if detail.get('rollout_valid', False):
            rollout_valid_count += 1
        else:
            rollout_invalid_count += 1
    
    # 打印统计结果
    print("\n" + "=" * 80)
    print("49个'都没生成对的'案例错误分析")
    print("=" * 80)
    
    print("\n1. 错误类别分布:")
    for category, count in error_categories.most_common():
        percentage = count / len(no_match_cases) * 100
        print(f"   {category}: {count} 个 ({percentage:.1f}%)")
    
    print("\n2. 错误类型分布:")
    for error_type, count in error_types.most_common():
        percentage = count / len(no_match_cases) * 100
        print(f"   {error_type}: {count} 个 ({percentage:.1f}%)")
    
    print("\n3. Reward分布:")
    for reward_level, count in sorted(reward_distribution.items()):
        percentage = count / len(no_match_cases) * 100
        print(f"   {reward_level}: {count} 个 ({percentage:.1f}%)")
    
    print(f"\n4. Rollout阶段执行状态:")
    print(f"   Rollout阶段有效: {rollout_valid_count} 个 ({rollout_valid_count/len(no_match_cases)*100:.1f}%)")
    print(f"   Rollout阶段无效: {rollout_invalid_count} 个 ({rollout_invalid_count/len(no_match_cases)*100:.1f}%)")
    print(f"   ⚠️  注意: {rollout_invalid_count} 个SQL在rollout阶段就执行失败，不应该被选中！")
    
    print(f"\n5. 执行错误统计:")
    print(f"   发现 {len(execution_errors)} 个有执行错误的案例")
    if execution_errors:
        print(f"   前5个执行错误案例:")
        for i, err in enumerate(execution_errors[:5], 1):
            rollout_status = "Rollout有效" if err.get('rollout_valid', False) else "Rollout无效"
            print(f"     {i}. 问题 {err['question_id']}: {err['error'][:70]}")
            print(f"        Reward: {err['reward']:.2f}, {rollout_status}")
    
    print(f"\n6. 字段缺失统计:")
    print(f"   发现 {len(field_missing_cases)} 个字段缺失的案例")
    if field_missing_cases:
        print(f"   前5个字段缺失案例:")
        for i, case in enumerate(field_missing_cases[:5], 1):
            missing = case['missing_fields']
            extra = case.get('extra_fields', [])
            print(f"     {i}. 问题 {case['question_id']}: 缺失 {missing}")
            if extra:
                print(f"        多余 {extra}")
    
    print(f"\n7. 逻辑错误统计:")
    print(f"   发现 {len(logic_error_cases)} 个逻辑错误案例")
    if logic_error_cases:
        print(f"   前5个逻辑错误案例:")
        for i, case in enumerate(logic_error_cases[:5], 1):
            print(f"     {i}. 问题 {case['question_id']}: {case.get('primary_issue', case['error_type'])}")
    
    # 8. 高Reward但错误的分析
    high_reward_wrong = [d for d in error_details if d.get('max_reward', 0) >= 0.8]
    print(f"\n8. 高Reward但错误的分析:")
    print(f"   发现 {len(high_reward_wrong)} 个高reward(>=0.8)但错误的案例")
    if high_reward_wrong:
        print(f"   按错误类别分布:")
        high_reward_categories = Counter(d.get('error_category', '未知') for d in high_reward_wrong)
        for cat, count in high_reward_categories.most_common():
            print(f"     {cat}: {count} 个")
        
        # 分析高reward但rollout无效的情况
        high_reward_invalid = [d for d in high_reward_wrong if not d.get('rollout_valid', True)]
        if high_reward_invalid:
            print(f"\n   ⚠️  严重问题: {len(high_reward_invalid)} 个高reward但rollout阶段就无效的案例")
            print(f"   这说明reward计算有bug，应该立即修复！")
            print(f"   前3个案例:")
            for i, case in enumerate(high_reward_invalid[:3], 1):
                print(f"     {i}. 问题 {case['question_id']}: Reward={case.get('max_reward', 0):.2f}, "
                      f"错误类型={case.get('error_type', '未知')}")
    
    # 9. 生成修复建议总结
    print(f"\n9. 修复建议总结:")
    suggestions_by_category = defaultdict(list)
    for detail in error_details:
        category = detail.get('error_category', '其他')
        suggestions = detail.get('fix_suggestions', [])
        if suggestions:
            suggestions_by_category[category].extend(suggestions)
    
    for category, suggestions in suggestions_by_category.items():
        unique_suggestions = list(set(suggestions))[:3]  # 每个类别最多3个建议
        if unique_suggestions:
            print(f"   {category}:")
            for sug in unique_suggestions:
                print(f"     - {sug}")
    
    # 保存详细结果
    output = {
        'summary': {
            'total_cases': len(no_match_cases),
            'error_category_distribution': dict(error_categories),
            'error_type_distribution': dict(error_types),
            'reward_distribution': dict(reward_distribution),
            'rollout_valid_count': rollout_valid_count,
            'rollout_invalid_count': rollout_invalid_count,
            'execution_errors_count': len(execution_errors),
            'field_missing_count': len(field_missing_cases),
            'logic_error_count': len(logic_error_cases)
        },
        'error_details': error_details,
        'execution_errors': execution_errors,
        'field_missing_cases': field_missing_cases,
        'logic_error_cases': logic_error_cases
    }
    
    output_file = error_analysis_file.replace('_error_analysis.json', '_no_match_detailed_analysis.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n[保存] 详细分析结果已保存到: {output_file}")
    
    # 生成Markdown报告
    markdown_file = output_file.replace('.json', '.md')
    generate_markdown_report(output, markdown_file)
    print(f"[保存] Markdown报告已保存到: {markdown_file}")
    
    return output


def generate_markdown_report(analysis_output: Dict, output_file: str):
    """生成Markdown格式的详细分析报告"""
    
    summary = analysis_output['summary']
    error_details = analysis_output['error_details']
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# 49个'都没生成对的'案例详细错误分析报告\n\n")
        f.write("## 摘要\n\n")
        f.write(f"- **总案例数**: {summary['total_cases']}\n")
        f.write(f"- **Rollout阶段有效**: {summary['rollout_valid_count']} 个\n")
        f.write(f"- **Rollout阶段无效**: {summary['rollout_invalid_count']} 个\n")
        f.write(f"- **执行错误数**: {summary['execution_errors_count']} 个\n")
        f.write(f"- **字段缺失数**: {summary['field_missing_count']} 个\n")
        f.write(f"- **逻辑错误数**: {summary['logic_error_count']} 个\n\n")
        
        f.write("## 错误类别分布\n\n")
        for category, count in sorted(summary['error_category_distribution'].items(), key=lambda x: x[1], reverse=True):
            percentage = count / summary['total_cases'] * 100
            f.write(f"- **{category}**: {count} 个 ({percentage:.1f}%)\n")
        
        f.write("\n## 错误类型分布\n\n")
        for error_type, count in sorted(summary['error_type_distribution'].items(), key=lambda x: x[1], reverse=True):
            percentage = count / summary['total_cases'] * 100
            f.write(f"- **{error_type}**: {count} 个 ({percentage:.1f}%)\n")
        
        f.write("\n## 详细案例分析\n\n")
        f.write("### 执行错误案例\n\n")
        execution_errors = analysis_output.get('execution_errors', [])
        for i, err in enumerate(execution_errors[:10], 1):
            f.write(f"#### 案例 {i}: 问题 {err['question_id']}\n\n")
            f.write(f"- **错误信息**: {err['error']}\n")
            f.write(f"- **Reward**: {err['reward']:.2f}\n")
            f.write(f"- **Rollout状态**: {'有效' if err.get('rollout_valid', False) else '无效'}\n\n")
        
        f.write("\n### 字段缺失案例\n\n")
        field_missing = analysis_output.get('field_missing_cases', [])
        for i, case in enumerate(field_missing[:10], 1):
            f.write(f"#### 案例 {i}: 问题 {case['question_id']}\n\n")
            f.write(f"- **缺失字段**: {', '.join(case['missing_fields'])}\n")
            if case.get('extra_fields'):
                f.write(f"- **多余字段**: {', '.join(case['extra_fields'])}\n")
            f.write(f"- **Reward**: {case['reward']:.2f}\n\n")
        
        f.write("\n### 逻辑错误案例\n\n")
        logic_errors = analysis_output.get('logic_error_cases', [])
        for i, case in enumerate(logic_errors[:10], 1):
            f.write(f"#### 案例 {i}: 问题 {case['question_id']}\n\n")
            f.write(f"- **错误类型**: {case['error_type']}\n")
            f.write(f"- **主要问题**: {case.get('primary_issue', '未知')}\n")
            f.write(f"- **Reward**: {case['reward']:.2f}\n\n")
        
        f.write("\n## 所有案例详情\n\n")
        f.write("| 问题ID | 错误类型 | 错误类别 | 主要问题 | Reward | Rollout有效 |\n")
        f.write("|--------|----------|----------|----------|--------|-------------|\n")
        
        for detail in sorted(error_details, key=lambda x: x.get('max_reward', 0), reverse=True):
            qid = detail['question_id']
            error_type = detail.get('error_type', '未知')
            error_category = detail.get('error_category', '未知')
            primary_issue = detail.get('primary_issue', '')[:50] + '...' if len(detail.get('primary_issue', '')) > 50 else detail.get('primary_issue', '')
            reward = detail.get('max_reward', 0)
            rollout_valid = '是' if detail.get('rollout_valid', False) else '否'
            f.write(f"| {qid} | {error_type} | {error_category} | {primary_issue} | {reward:.2f} | {rollout_valid} |\n")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='分析所有49个"都没生成对的"案例')
    parser.add_argument('--error_analysis_file', type=str, required=True,
                        help='错误分析结果文件路径')
    parser.add_argument('--result_file', type=str, required=True,
                        help='原始结果文件路径')
    parser.add_argument('--eval_file', type=str, required=True,
                        help='评估结果文件路径')
    
    args = parser.parse_args()
    
    analyze_all_no_match_cases(
        args.error_analysis_file,
        args.result_file,
        args.eval_file
    )


if __name__ == '__main__':
    main()
