# NL2SQL System

可运行的baseline. Alpha-SQL-2.2.4.

项目git clone https://github.com/DobbySquirrel/SelfCorrectionSQL.git

数据集: wget https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip

`.env` 文件读取 `DB_ROOT_DIR` 环境变量来查找数据库。DB_ROOT_DIR=/bird/dev_20240627/dev_databases


数据库准确度

模型调用

mcts框架
1. Mcts 框架 V1

# 进入项目根目录
cd /home/shenshuyu/SQL_tool_multiAgent

# 案例测试
python workflows/mcts_v1/test/test_mcts.py \
  --ppl_file data/subset_ppl_dev_python.json \
  --sql_out workflows/mcts_v1/test/out/test_single.txt \
  --json_out workflows/mcts_v1/test/out/test_single.json \
  --qid 25 \
  --gold_file data/sub_sampled_bird_dev_set.json \
  --parallel_workers 5 \
  --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1"


# 策略模式测试（无策略模式）<需要测>：
nohup python workflows/mcts_v1/test/test_mcts.py \
   --ppl_file data/subset_ppl_dev_python.json \
   --sql_out workflows/mcts_v1/test/out/1_6_test_no_strategy_sql.txt \
   --json_out workflows/mcts_v1/test/out/1_6_test_no_strategy_result.json \
   --gold_file data/sub_sampled_bird_dev_set.json \
   --parallel_workers 5 \
   --strategy_mode NONE \
   --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1" \
   > workflows/mcts_v1/test/out/1_6_test_no_strategy.log 2>&1 &

# 策略模式测试（LLM选择策略） <需要测>：
nohup python workflows/mcts_v1/test/test_mcts.py \
   --ppl_file data/subset_ppl_dev_python.json \
   --sql_out workflows/mcts_v1/test/out/1_6_test_with_strategy_sql.txt \
   --json_out workflows/mcts_v1/test/out/1_6_test_with_strategy_result.json \
   --gold_file data/sub_sampled_bird_dev_set.json \
   --parallel_workers 5 \
   --strategy_mode LLM_PICK_ONCE \
   --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1" \
   > workflows/mcts_v1/test/out/1_6_test_with_strategy.log 2>&1 &


2. MCts 框架 V2
To do
