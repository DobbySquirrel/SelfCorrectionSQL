import json
import sys
from collections import defaultdict

def analyze_json_structure(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 分析数据结构
        structure_info = {
            'type': type(data).__name__,
            'length': len(data) if isinstance(data, (list, dict)) else None,
            'keys': list(data.keys()) if isinstance(data, dict) else None,
            'sample_item': None
        }
        
        # 获取样本数据
        if isinstance(data, list) and len(data) > 0:
            structure_info['sample_item'] = data[0]
        elif isinstance(data, dict) and len(data) > 0:
            first_key = next(iter(data))
            structure_info['sample_item'] = data[first_key]
            
        return structure_info
    except Exception as e:
        return f"Error reading file: {str(e)}"

def main():
    file1 = "/home/shenshuyu/SQL_tool/csc_sql/outputs/genetic_output_6_14/generation_1.json"
    file2 = "/home/shenshuyu/SQL_tool/csc_sql/outputs/genetic_output_6_15_2/generation_1.json"
    
    print("分析第一个文件:")
    structure1 = analyze_json_structure(file1)
    print(json.dumps(structure1, indent=2, ensure_ascii=False))
    
    print("\n分析第二个文件:")
    structure2 = analyze_json_structure(file2)
    print(json.dumps(structure2, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main() 