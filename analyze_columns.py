import json
import os

def analyze_missing_columns(file_path):
    if not os.path.exists(file_path):
        print(f"❌ 错误: 找不到文件 {file_path}")
        return

    try:
        print(f"📊 正在加载文件: {os.path.basename(file_path)} ...")
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # --- 定位数据源 ---
        error_list = []
        if isinstance(data, dict):
            if 'error_details' in data:
                error_list = data['error_details']
            elif 'data' in data:
                error_list = data['data']
        elif isinstance(data, list):
            error_list = data
            
        if not error_list:
            print("❌ 无法找到数据列表。")
            return

        # --- 开始分析 ---
        total_failed = 0
        missing_col_count = 0
        cases = []

        print(f"🔍 正在扫描少选列 (Missing Columns) 的情况...\n")

        for item in error_list:
            # 只看失败案例
            if item.get('success', 0) == 0:
                total_failed += 1
                
                idx = item.get('idx', 'N/A')
                qid = item.get('question_id', 'N/A')
                pred_res = item.get('predicted_res', [])
                gt_res = item.get('ground_truth_res', [])

                if pred_res and gt_res and len(pred_res) > 0 and len(gt_res) > 0:
                    try:
                        pred_row = pred_res[0]
                        gt_row = gt_res[0]
                        
                        if isinstance(pred_row, list) and isinstance(gt_row, list):
                            p_cols = len(pred_row)
                            g_cols = len(gt_row)
                            
                            # 🔥 核心修改：预测列数 < 标准列数
                            if p_cols < g_cols:
                                missing_col_count += 1
                                cases.append({
                                    "idx": idx,
                                    "q_id": qid,
                                    "pred_cols": p_cols,
                                    "gt_cols": g_cols,
                                    "diff": g_cols - p_cols
                                })
                    except Exception:
                        continue

        # --- 输出结果 ---
        print("=" * 60)
        print("🔵 少选列 (Under-Selection) 分析报告")
        print("=" * 60)
        print(f"总失败案例数       : {total_failed}")
        print(f"少选列导致的错误数 : {missing_col_count}")
        
        if total_failed > 0:
            ratio = (missing_col_count / total_failed) * 100
            print(f"错误占比           : {ratio:.2f}%")
        
        print("\n📝 详细案例列表 (IDX | Q_ID | Pred vs GT):")
        print("-" * 60)
        for case in cases:
            print(f"Idx: {case['idx']:<6} | QID: {case['q_id']:<6} | 你的列数: {case['pred_cols']} vs 标准: {case['gt_cols']} (少 {case['diff']} 列)")
        print("-" * 60)

    except Exception as e:
        print(f"❌ 脚本执行出错: {e}")

if __name__ == "__main__":
    TARGET_FILE = '/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_11_acc.json'
    analyze_missing_columns(TARGET_FILE)