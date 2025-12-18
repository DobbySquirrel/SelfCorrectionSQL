#!/usr/bin/env python3
"""
快速测试MCTS改进后的性能 - 20个样本
使用data/subset_ppl_dev.json格式的数据（包含完整的schema和foreign_key信息）
"""

import json
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# 添加项目根目录到系统路径
sys.path.append(str(Path(__file__).parent.parent.parent))

def create_test_data():
    """创建20个样本的测试数据"""
    # 使用sub_sampled_bird_dev_set.json获取question_id和db_id
    bird_file = Path(__file__).parent.parent.parent / "data" / "sub_sampled_bird_dev_set.json"
    # 使用subset_ppl_dev.json获取schema信息
    ppl_file = Path(__file__).parent.parent.parent / "data" / "subset_ppl_dev.json"
    
    if not bird_file.exists() or not ppl_file.exists():
        print("❌ 找不到数据文件，请检查路径")
        return None
    
    print(f"📂 读取数据文件: {bird_file} 和 {ppl_file}")
    
    # 读取两个文件
    with open(bird_file, 'r', encoding='utf-8') as f:
        bird_data = json.load(f)
    with open(ppl_file, 'r', encoding='utf-8') as f:
        ppl_data = json.load(f)
    
    # 创建db到ppl数据的映射
    db_to_ppl = {}
    for item in ppl_data:
        db_to_ppl[item['db']] = item
    
    # 合并数据：取前20个样本
    test_data = []
    for i, bird_item in enumerate(bird_data[:20]):
        db_id = bird_item['db_id']
        question_id = bird_item['question_id']
        
        # 查找对应的ppl数据
        if db_id in db_to_ppl:
            ppl_item = db_to_ppl[db_id]
            # 合并数据
            merged_item = {
                'question_id': question_id,
                'db': db_id,
                'question': bird_item['question'],
                'simplified_ddl': ppl_item['simplified_ddl'],
                'foreign_key': ppl_item['foreign_key'],
                'evidence': ppl_item.get('evidence', ''),
            }
            test_data.append(merged_item)
        else:
            print(f"⚠️ 找不到db_id {db_id} 对应的schema信息")
    
    # 创建临时测试文件
    test_file = Path(__file__).parent / "out" / "test_20_samples.json"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(test_file, 'w', encoding='utf-8') as f:
        json.dump(test_data, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 创建测试数据: {test_file} ({len(test_data)}个样本)")
    return str(test_file)

def _save_intermediate_results(output_dir, test_data_file, results):
    """保存中间结果（每完成一个样本就保存）"""
    try:
        with open(test_data_file, 'r', encoding='utf-8') as f:
            original_samples = json.load(f)
        
        json_result = {}
        for i in range(len(results)):
            sample = original_samples[i]
            question_id = str(sample.get('question_id', i + 1))
            db_name = sample.get('db', '')
            sql = results[i] if results[i] else ""
            json_result[question_id] = f"{sql}\t----- bird -----\t{db_name}"
        
        # 保存到临时文件
        result_file = output_dir / "mcts_sql_20_samples.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(json_result, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"保存中间结果失败: {e}")

def run_test():
    """运行20个样本的MCTS测试"""
    
    # 创建输出目录
    output_dir = Path(__file__).parent / "out" / "mcts_test_20"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建测试数据
    test_data_file = create_test_data()
    if not test_data_file:
        print("❌ 无法创建测试数据")
        return
    
    print("\n" + "=" * 80)
    print("🧪 MCTS改进后测试 - 20个样本")
    print("=" * 80)
    
    # 导入并运行测试
    from workflows.mcts.test.test_mcts_workflow import process_single_sample_mcts
    from workflows.mcts.mcts_workflow import MCTSWorkflow
    from core.database_connector import DatabaseConnector
    from utils.model_utils import get_llm_config
    
    # 读取测试数据
    with open(test_data_file, 'r', encoding='utf-8') as f:
        test_samples = json.load(f)
    
    print(f"\n开始处理 {len(test_samples)} 个样本...")
    
    # 准备任务列表
    tasks = []
    for i in range(min(20, len(test_samples))):
        sample = test_samples[i]
        # 数据已经包含正确的question_id，直接使用
        tasks.append((i, sample))
    
    # 初始化结果数组
    num_samples = len(tasks)
    results = [""] * num_samples
    
    # 并行执行
    print(f"\n🚀 开始并行处理 {num_samples} 个样本...")
    # MCTS建议使用1-2个worker，避免资源竞争和数据库连接冲突
    # 如果需要更快，可以增加worker数量，但可能影响性能
    max_workers = 32  # 改为2或更多可以加速
    print(f"   使用 {max_workers} 个worker")
    
    # 导入锁，用于中间保存
    import threading
    save_lock = threading.Lock()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(process_single_sample_mcts, (idx, ppl, 1)): idx 
            for idx, ppl in tasks if ppl is not None
        }
        
        for future in tqdm(as_completed(future_to_idx), total=len(future_to_idx), desc="MCTS生成SQL"):
            idx = future_to_idx[future]
            try:
                i, sql = future.result()
                results[idx] = sql
                print(f"\n✅ 样本 {idx+1} 完成")
                
                # 每完成一个样本就保存一次，避免卡住时丢失结果
                with save_lock:
                    _save_intermediate_results(output_dir, test_data_file, results)
                print(f"💾 结果已保存")
                    
            except Exception as e:
                results[idx] = ""
                print(f"\n❌ 样本 {idx+1} 失败: {e}")
                # 即使失败也保存
                with save_lock:
                    _save_intermediate_results(output_dir, test_data_file, results)
    
    # 保存JSON结果
    json_result = {}
    
    with open(test_data_file, 'r', encoding='utf-8') as f:
        original_samples = json.load(f)
    
    for i in range(len(results)):
        sample = original_samples[i]
        question_id = str(sample.get('question_id', i + 1))
        db_name = sample.get('db', '')
        sql = results[i] if results[i] else ""
        
        # 格式: "SQL\t----- bird -----\tdb_name"
        json_result[question_id] = f"{sql}\t----- bird -----\t{db_name}"
    
    # 保存JSON文件
    result_file = output_dir / "mcts_sql_20_samples.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(json_result, f, indent=4, ensure_ascii=False)
    
    # 统计
    print("\n" + "=" * 80)
    print("📊 测试结果统计")
    print("=" * 80)
    
    generated_count = sum(1 for sql in results if sql and sql.strip())
    print(f"   - 总样本数: {len(results)}")
    print(f"   - 成功生成: {generated_count}")
    print(f"   - 生成率: {generated_count/len(results)*100:.1f}%")
    print(f"   - 结果文件: {result_file}")
    
    print("\n✅ 测试完成!")


if __name__ == "__main__":
    run_test()
