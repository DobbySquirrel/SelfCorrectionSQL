import json
import sys
from pathlib import Path
import pandas as pd
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import autogen
import yaml

def load_config(config_path):
    """加载配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config

def setup_llm_config(config):
    """设置LLM配置"""
    return {
        "config_list": [
            {
                "model": config.get("model", "gpt-4o-mini"),
                "api_key": config.get("api", ""),
                "base_url": config.get("base_url", "https://api.chsdw.top/v1/chat/completions")
            }
        ],
        "temperature": 0.7,
    }

def format_sql_result(result_df, error_message=None):
    """格式化SQL执行结果"""
    if result_df is not None:
        return result_df.to_dict(orient='records')
    return f"Error: {error_message}"

def get_examples_from_dataset(question_id, dataset_path):
    """从数据集中获取对应question_id的few-shot示例"""
    with open(dataset_path, 'r', encoding='utf-8') as f:
        dataset = json.load(f)
    
    for item in dataset:
        if str(item["question_id"]) == str(question_id):
            return item.get("examples", [])
    return []

def format_examples(examples):
    """格式化few-shot示例"""
    if not examples:
        return ""
    
    formatted = "\nFew-shot examples:\n"
    for ex in examples:
        formatted += f"""
Question: {ex['question']}
Evidence: {ex.get('evidence', '')}
SQL: {ex['sql']}
"""
    return formatted

def analyze_example(example, llm_config):
    """分析单个示例的函数"""
    sql_analyzer = autogen.AssistantAgent(
        name="SQLAnalyzer",
        llm_config=llm_config,
        system_message="""你是一个SQL分析专家。你的任务是分析给定的SQL查询为什么是正确的答案。"""
    )
    
    user_proxy = autogen.UserProxyAgent(
        name="user_proxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=0,
        code_execution_config=False
    )
    
    analysis_prompt = f"""
请分析以下SQL查询为什么是正确的答案：

问题：{example['question']}
SQL：{example['sql']}

请提供详细的分析，例如:
1. SQL输出符合答案格式，没有画蛇添足，没有添加辅助逻辑（你只要关注主语，不要关注谓语）。
2. 问题里有list，则SQL返回list明确的要求即可，对list外的描述需求（例如who ,where 介词后如何判断该list是需要的）这些都是多余的,现SQL符合要求。
3. CONCAT或者case when可以是数字计算，但是不能是字符赋值,例如THEN 'Yes' ELSE 'No'这种字符直接赋值是不允许的,现SQL符合要求。
4. 不存在columns, string的拼接也是不需要的，SQL按顺序返回。
5. SQL返回的顺序和问题中要求的顺序一致。
6. SQL虽然有Order,group by,limit会导致隐式错误，但是在现数据库中是是正确的。
等等分析方面...
请用以下格式回答：
<analysis>
你的详细分析
</analysis>
"""
    
    user_proxy.initiate_chat(sql_analyzer, message=analysis_prompt)
    analysis_result = user_proxy.last_message(sql_analyzer)
    
    from utils.agent_helpers import AgentHelpers
    analysis = AgentHelpers.extract_xml_tag(analysis_result, "analysis")
    
    return example, analysis

def main():
    # 设置路径
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/6_2")
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = Path(__file__).parent / "config" / "config.yaml"
    
    # 加载配置
    config = load_config(config_path)
    llm_config = setup_llm_config(config)
    
    # 加载few-shot数据集
    few_shot_dataset_path = "/home/shenshuyu/SQL_tool/data/example/sub_few_shot.json"
    with open(few_shot_dataset_path, 'r', encoding='utf-8') as f:
        dataset = json.load(f)
    
    # 创建所有任务的列表
    all_examples = []
    for item in dataset:
        all_examples.extend([(ex, llm_config) for ex in item['examples']])
    
    # 使用线程池处理任务
    with ThreadPoolExecutor(max_workers=4) as executor:
        # 提交所有任务
        future_to_example = {
            executor.submit(analyze_example, ex, llm_config): (item_idx, ex_idx)
            for item_idx, item in enumerate(dataset)
            for ex_idx, ex in enumerate(item['examples'])
        }
        
        # 使用tqdm显示进度
        for future in tqdm(as_completed(future_to_example), total=len(future_to_example), desc="分析问题集"):
            item_idx, ex_idx = future_to_example[future]
            try:
                example, analysis = future.result()
                dataset[item_idx]['examples'][ex_idx]['analysis'] = analysis
                
                # 定期保存结果
                with open(output_dir / "analyzed_few_shot.json", 'w', encoding='utf-8') as f:
                    json.dump(dataset, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"处理示例时发生错误: {e}")
    
    print(f"分析完成！结果已保存到 {output_dir}/analyzed_few_shot.json")

if __name__ == "__main__":
    main() 