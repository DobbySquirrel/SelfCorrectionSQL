import json
from pathlib import Path

# 文件路径
json_path = Path("/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/bc_results.json")
baseline_out = json_path.parent / "a.txt"
entropy_out = json_path.parent / "b.txt"

# 读取 JSON
with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

# 写出到 a.txt 和 b.txt
with open(baseline_out, "w", encoding="utf-8") as fa, open(entropy_out, "w", encoding="utf-8") as fb:
    for v in data.values():
        baseline_sql = v.get("serial_sql", "").replace("\n", " ").strip()
        entropy_sql = v.get("parallel_sql", "").replace("\n", " ").strip()
        fa.write(f"{baseline_sql}\n")
        fb.write(f"{entropy_sql}\n")

print(f"✅ 已输出到:\n  {baseline_out}\n  {entropy_out}")
