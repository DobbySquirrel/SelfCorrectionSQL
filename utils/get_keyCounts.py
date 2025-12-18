import sqlglot
import json
from collections import Counter

def extract_operators_with_sqlglot(sql):
    found = []
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
        if not parsed:
            return found
        for node in parsed.walk():
            found.append(node.key)
            if node.key == "function":
                func_name = node.name.upper() if hasattr(node, "name") else None
                if func_name:
                    found.append(func_name)
            elif node.key == "anonymous":
                # 处理窗口函数，如DENSE_RANK(), RANK(), ROW_NUMBER()等
                func_name = node.name.upper() if hasattr(node, "name") else None
                if func_name:
                    found.append(func_name)
            if node.key == "join":
                join_type = node.args.get("kind")
                if join_type:
                    found.append(f"{join_type.upper()} JOIN")
    except Exception as e:
        print(f"解析出错: {e}\n  SQL内容: {sql}")
    return found

def count_operators_in_json_file(filepath):
    op_counter = Counter()
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
        for idx, item in enumerate(data, 1):
            sql = item.get("SQL", "").strip()
            if not (sql.upper().startswith("SELECT") or sql.upper().startswith("WITH")):
                print(f"第{idx}条不是SELECT/WITH开头，内容为：{sql}")
                continue
            ops = extract_operators_with_sqlglot(sql)
            op_counter.update(ops)
    return op_counter

if __name__ == "__main__":
    files = [
        '/home/shenshuyu/SQL_dataset/train/train/train.json',
        '/home/shenshuyu/SQL_dataset/dev_20240627/dev.json'
    ]
    total_counter = Counter()
    for file in files:
        counter = count_operators_in_json_file(file)
        print(f"{file} 操作符统计：")
        for op, count in counter.most_common():
            print(f"{op}: {count}")
        print('-' * 40)
        total_counter.update(counter)
    print("总计操作符统计：")
    for op, count in total_counter.most_common():
        print(f"{op}: {count}")