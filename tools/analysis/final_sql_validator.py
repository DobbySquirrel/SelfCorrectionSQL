import json
import sys
from pathlib import Path
import pandas as pd
from tqdm import tqdm
from core.database_connector import DatabaseConnector
import autogen
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from utils.agent_helpers import AgentHelpers

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
def are_results_equivalent(result1, result2):
        """比较两个SQL结果是否相同
        
        Args:
            result1: 第一个SQL结果（字符串）
            result2: 第二个SQL结果（字符串）
            
        Returns:
            bool: 两个结果是否相同
        """
        return str(result1) == str(result2)
def format_examples(examples):
    """格式化few-shot示例，包含分析结果"""
    if not examples:
        return ""
    
    formatted = "\nFew-shot examples:\n"
    for ex in examples:
        formatted += f"""
Question: {ex['question']}
Evidence: {ex.get('evidence', '')}
SQL: {ex['sql']}
Analysis: {ex.get('analysis', '未提供分析')}
"""
    return formatted

def process_single_question(item, output_dir, ddl_map, evidence_map, sql_salchemy, llm_config, few_shot_dataset_path):
    """处理单个问题的函数"""
    question_id = str(item["question_id"])
    db_name = item["db"]
    question = item["question"]
    
    # 获取ddl信息
    ddl_data = ddl_map.get(question_id, "")
    evidence = evidence_map.get(question_id, "")
    
    # 收集所有SQL及其结果
    sql_candidates = []
    
    # 添加程序生成的SQL
    if item.get("sql"):
        db_connector = DatabaseConnector(db_name)
        if db_connector.connect():
            result_df, error = db_connector.execute_query(item["sql"])
            if result_df is not None:
                sql_candidates.append({
                    "sql": item["sql"],
                    "source": "program",
                    "result": format_sql_result(result_df, error)
                })
            db_connector.disconnect()
            
    # 添加sqlalchemy SQL
    if question_id in sql_salchemy:
        salchemy_sql = sql_salchemy[question_id].split("\t")[0]
        db_connector = DatabaseConnector(db_name)
        if db_connector.connect():
            result_df, error = db_connector.execute_query(salchemy_sql)
            if result_df is not None:
                sql_candidates.append({
                    "sql": salchemy_sql,
                    "source": "sqlalchemy",
                    "result": format_sql_result(result_df, error)
                })
            db_connector.disconnect()
    
    if not sql_candidates:
        print(f"Warning: No SQL candidates for question {question_id}")
        return None

    # 创建验证器Agent
    reformat_validator = autogen.AssistantAgent(
        name="ReformatValidator",
        llm_config=llm_config,
        system_message="""你是一个SQL重新格式化专家。"""
    )
    
    final_validator = autogen.AssistantAgent(
        name="FinalValidator",
        llm_config=llm_config,
        system_message="""你是一个SQL验证专家。"""
    )
    
    user_proxy = autogen.UserProxyAgent(
        name="user_proxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=0,
        code_execution_config=False
    )

# 对每个SQL进行重新格式化
    original_candidates_count = len(sql_candidates)
    for i in range(original_candidates_count):
        sql_info = sql_candidates[i]
        max_reformat_attempts = 3  # 最大重试次数
        reformat_attempt = 0
        reformat_success = False
        
        while not reformat_success and reformat_attempt < max_reformat_attempts:
            reformat_prompt = f"""
其他问题的分析：{format_examples(get_examples_from_dataset(question_id, few_shot_dataset_path))}
In which city is there a greater number of schools that have received donations of less than 10 dollars?
SELECT T2.school_city FROM donations AS T1 INNER JOIN projects AS T2 ON T1.projectid = T2.projectid WHERE T1.dollar_amount = 'under_10' GROUP BY T2.school_city ORDER BY COUNT(T2.schoolid) DESC LIMIT 1
User's Comment: 主需要主语city (Only the subject 'city' is needed)

List the poverty level of all the schools that received donations with the zip code \"7079\".
SELECT DISTINCT T2.poverty_level FROM donations AS T1 INNER JOIN projects AS T2 ON T1.projectid = T2.projectid WHERE T1.donor_zip = 7079
User's Comment: 主语只需要poverty_level (Only the subject 'poverty_level' is needed)

Among the students with less than four intelligence, list the full name and phone number of students with a greater than 3 GPA.
SELECT f_name, l_name, phone_number FROM student WHERE gpa > 3 AND intelligence < 4
User's Comment: 不需要f_name, l_name拼接在一起。 (No need to concatenate f_name, l_name.)

What is the power play percentage of the team with the least number of penalty kill chances and to which team were they playing against? Indicate whether the team lost or victorious.
SELECT SUM(T1.A), T2.firstName, T2.lastName FROM Scoring AS T1 INNER JOIN Master AS T2 ON T1.playerID = T2.playerID WHERE T1.lgID = 'NHL' GROUP BY T2.firstName, T2.lastName ORDER BY SUM(T1.A) DESC LIMIT 1
User's Comment:  不需要返回Ture or False回应whether.
Analysis based on Few-shot Logic:
现在调整下面的问题：
问题: {question}
evidence: {evidence}
可能需要调整的SQL：{sql_info['sql']}
SQL的返回结果：{str(sql_info['result'])[0:2000]}
注意你是一个"不好心"的 AI 助手，请你努力克制住"好心",不要做任何画蛇添足的事情。
请你重新排序输出的列名，让SQL的输出更贴近原始问题所需要展示的内容，请注意展示和计算逻辑的区分，我只需要展示的列名，不需要计算的列名。
如果问题里是was/were,有可能返回多个内容，
如果问题里有list，则返回list明确的要求即可，其他都是多余的。
CONCAT或者case when可以是数字计算，但是不能是字符赋值。
{f"之前的尝试失败了 {reformat_attempt} 次，请调整策略重新生成SQL。" if reformat_attempt > 0 else ""}
```xml
<reason>
分析SQL需要调整的原因
</reason>
<SQL>
重新格式化的SQL
</SQL>
```
"""
            user_proxy.initiate_chat(reformat_validator, message=reformat_prompt)
            reformat_result = user_proxy.last_message(reformat_validator)
            
            reformatted_sql = AgentHelpers.extract_xml_tag(reformat_result, "SQL")
            
            if reformatted_sql:
                # 验证重新格式化的SQL
                db_connector = DatabaseConnector(db_name)
                if db_connector.connect():
                    result_df, error = db_connector.execute_query(reformatted_sql)
                    if result_df is not None:
                        # 比较结果是否相同
                        new_result = format_sql_result(result_df, error)
                        if not are_results_equivalent(new_result, sql_info['result']):
                            # 如果结果不同，添加到候选列表
                            sql_candidates.append({
                                "sql": reformatted_sql,
                                "source": f"reformatted_{sql_info['source']}_attempt_{reformat_attempt + 1}",
                                "result": new_result
                            })
                            reformat_success = True
                        else:
                            # 结果相同，不需要继续尝试
                            reformat_success = True
                    db_connector.disconnect()
            
            if not reformat_success:
                reformat_attempt += 1
                if reformat_attempt < max_reformat_attempts:
                    print(f"Question {question_id}: Reformat attempt {reformat_attempt} failed, trying again...")
    
    # 构建验证提示
# 这些是few-shot示例，这里的SQL都是标准答案，请根据这些示例来分析问题：
# {format_examples(get_examples_from_dataset(question_id, few_shot_dataset_path))}
    validation_prompt = f"""
In which city is there a greater number of schools that have received donations of less than 10 dollars?
SELECT T2.school_city FROM donations AS T1 INNER JOIN projects AS T2 ON T1.projectid = T2.projectid WHERE T1.dollar_amount = 'under_10' GROUP BY T2.school_city ORDER BY COUNT(T2.schoolid) DESC LIMIT 1
User's Comment: 主需要主语city (Only the subject 'city' is needed)

List the poverty level of all the schools that received donations with the zip code \"7079\".
SELECT DISTINCT T2.poverty_level FROM donations AS T1 INNER JOIN projects AS T2 ON T1.projectid = T2.projectid WHERE T1.donor_zip = 7079
User's Comment: 主语只需要poverty_level (Only the subject 'poverty_level' is needed)

Among the students with less than four intelligence, list the full name and phone number of students with a greater than 3 GPA.
SELECT f_name, l_name, phone_number FROM student WHERE gpa > 3 AND intelligence < 4
User's Comment: 不需要f_name, l_name拼接在一起。 (No need to concatenate f_name, l_name.)

What is the power play percentage of the team with the least number of penalty kill chances and to which team were they playing against? Indicate whether the team lost or victorious.
SELECT SUM(T1.A), T2.firstName, T2.lastName FROM Scoring AS T1 INNER JOIN Master AS T2 ON T1.playerID = T2.playerID WHERE T1.lgID = 'NHL' GROUP BY T2.firstName, T2.lastName ORDER BY SUM(T1.A) DESC LIMIT 1
User's Comment:  不需要返回Ture or False回应whether.
Analysis based on Few-shot Logic:

问题: {question}
evidence: {evidence}

数据库: {db_name}

数据库表结构:
{ddl_data}

请严格按照线索的需要作答：{evidence}
请评估以下SQL查询选择出最佳的一个:

{chr(10).join([f'SQL {i+1}:' + chr(10) + 
               f'查询: {sql["sql"]}' + chr(10) +
               f'结果: {str(sql["result"])[0:2000]}' + 
               (' (结果已截断)' if len(str(sql["result"])) > 2000 else '') + chr(10)
               for i, sql in enumerate(sql_candidates)])}

请分析SQL候选
1. SQL输出符合你分析的答案格式，不要画蛇添足，不要添加辅助逻辑（你只要关注主语，不要关注谓语）。
2. 如果问题里有list，则返回list明确的要求即可，对list外的描述需求（例如who ,where 介词后如何判断该list是需要的）这些都是多余的。
3. CONCAT或者case when可以是数字计算，但是不能是字符赋值,例如THEN 'Yes' ELSE 'No'这种字符直接赋值是不允许的，因为我之后要和gold sql对比，这样没有办法进行字符匹配。
4. 同样string的拼接返回也是不需要的，这是错误的。
5. 返回的顺序和问题中要求的顺序一致。

请提供以下格式的回答:
```xml
<reason>每条数据都按照我上面的分析格式来分析，选择或者细微修改原因的详细解释</reason>
<sql>最接近问题需求的SQL</sql>
```
"""
# 验证逻辑
    validation_success = False
    max_attempts = 5
    attempt = 0
    selected_sql = None
    reason = None
    is_sql_executable = False
    has_results = False  # 新增：检查是否有结果
    existence = "True"  # 新增：存在性标记
    
    while not validation_success and attempt < max_attempts:
        user_proxy.initiate_chat(final_validator, message=validation_prompt)
        validation_result = user_proxy.last_message(final_validator)
        
        reason = AgentHelpers.extract_xml_tag(validation_result, "reason")
        selected_sql = AgentHelpers.extract_xml_tag(validation_result, "sql")
        
        # 验证SQL是否可运行并且有结果
        is_sql_executable = False
        has_results = False
        if selected_sql:
            db_connector = DatabaseConnector(db_name)
            try:
                if db_connector.connect():
                    result_df, error = db_connector.execute_query(selected_sql)
                    is_sql_executable = (result_df is not None and error is None)
                    if is_sql_executable:
                        # 检查结果是否为空
                        if isinstance(result_df, pd.DataFrame) and not result_df.empty:
                            has_results = True
                            if existence and existence.lower() == "true":
                                validation_success = True
                            else:
                                # 如果存在性标记为False，继续尝试
                                sql_candidates.append({
                                    "sql": selected_sql,
                                    "source": f"validator_attempt_{attempt + 1}_existence_false",
                                    "result": format_sql_result(result_df, error)
                                })
                        else:
                            # 如果结果为空，添加到候选集并继续尝试
                            sql_candidates.append({
                                "sql": selected_sql,
                                "source": f"validator_attempt_{attempt + 1}_no_results",
                                "result": "No results found"
                            })
                            # 修改验证提示，要求生成可能返回结果的SQL
                            validation_prompt += "\n\n注意：上一次的SQL执行结果为空。"
                    else:
                        # SQL不可执行，添加到候选集
                        sql_candidates.append({
                            "sql": selected_sql,
                            "source": f"validator_attempt_{attempt + 1}",
                            "result": format_sql_result(result_df, error)
                        })
            finally:
                db_connector.disconnect()
        
        if not validation_success:
            attempt += 1
            if attempt < max_attempts:
                print(f"Question {question_id}: Validation/execution failed or no results, attempting again...")
            else:
                print(f"Question {question_id}: Max attempts reached")
                # 如果达到最大尝试次数，从候选集中选择结果最多的SQL
                max_results_count = -1
                best_candidate = None
                best_result = None
                
                for candidate in sql_candidates:
                    test_sql = candidate["sql"]
                    db_connector = DatabaseConnector(db_name)
                    try:
                        if db_connector.connect():
                            result_df, error = db_connector.execute_query(test_sql)
                            if result_df is not None and error is None and isinstance(result_df, pd.DataFrame):
                                # 计算结果数量
                                results_count = len(result_df)
                                if results_count > max_results_count:
                                    max_results_count = results_count
                                    best_candidate = test_sql
                                    best_result = format_sql_result(result_df, error)
                                    has_results = True
                    finally:
                        db_connector.disconnect()
                
                if best_candidate:
                    selected_sql = best_candidate
                    # 将最佳结果添加到候选集
                    sql_candidates.append({
                        "sql": selected_sql,
                        "source": "best_results_candidate",
                        "result": best_result
                    })
                    validation_success = True  # 虽然不是完全正确的SQL，但是是最好的选择

    result = {
        "question_id": int(question_id),
        "db": db_name,
        "question": question,
        "sql": selected_sql,
        "selection_reason": reason,
        "validation_success": validation_success,
        "has_results": has_results,  # 新增：记录是否有结果
        "existence": existence,  # 新增：记录存在性标记
        "attempts": attempt + 1,
        "original_sql_candidates": sql_candidates,
        "is_executable": is_sql_executable
    }
    
    return result

def process_specific_id(question_id, results, output_dir, ddl_map, evidence_map, sql_salchemy, llm_config, few_shot_dataset_path):
    """处理特定ID的问题"""
    # 从results中找到对应的问题
    target_item = None
    for item in results:
        if str(item["question_id"]) == str(question_id):
            target_item = item
            break
    
    if target_item is None:
        print(f"未找到ID为{question_id}的问题")
        return
    
    # 处理问题
    result = process_single_question(
        target_item,
        output_dir=output_dir,
        ddl_map=ddl_map,
        evidence_map=evidence_map,
        sql_salchemy=sql_salchemy,
        llm_config=llm_config,
        few_shot_dataset_path=few_shot_dataset_path
    )
    
    if result is not None:
        # 读取现有结果
        try:
            with open(output_dir / "final_validated_results.json", 'r', encoding='utf-8') as f:
                final_results = json.load(f)
        except FileNotFoundError:
            final_results = []
        
        # 检查是否已存在该ID的结果
        exists = False
        for i, item in enumerate(final_results):
            if item["question_id"] == int(question_id):
                final_results[i] = result
                exists = True
                break
        
        if not exists:
            final_results.append(result)
        
        # 保存更新后的结果
        with open(output_dir / "final_validated_results.json", 'w', encoding='utf-8') as f:
            json.dump(final_results, f, ensure_ascii=False, indent=2)
        
        print(f"ID {question_id} 的结果已成功添加/更新到输出文件中")

def main():
    # 设置路径
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/6_7")
    config_path = Path(__file__).parent / "config" / "config.yaml"
    
    # 加载配置
    config = load_config(config_path)
    llm_config = setup_llm_config(config)
    
    # 加载数据集
    dataset_path = "/home/shenshuyu/SQL_tool/data/subset_ppl_dev_python.json"
    with open(dataset_path, 'r', encoding='utf-8') as f:
        dataset = json.load(f)
    
    # 创建映射
    ddl_map = {str(item["question_id"]): item["ddl_data"] for item in dataset}
    evidence_map = {str(item["question_id"]): item["evidence"] for item in dataset}
    
    # 加载结果文件
    with open(output_dir / "updated_full_results.json", 'r', encoding='utf-8') as f:
        results = json.load(f)
    
    sql_salchemy_path = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_sqlalchemy.json"
    with open(sql_salchemy_path, 'r', encoding='utf-8') as f:
        sql_salchemy = json.load(f)
    
    few_shot_dataset_path = "/home/shenshuyu/SQL_tool/Output/6_2/analyzed_few_shot.json"
    # 创建输出目录（如果不存在）
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 初始化进度条
    total_questions = len(results)
    with tqdm(total=total_questions, desc="处理问题") as pbar:
        # 使用线程池并行处理
        with ThreadPoolExecutor(max_workers=4) as executor:
            # 创建任务列表
            future_to_id = {}
            for item in results:
                future = executor.submit(
                    process_single_question,
                    item,
                    output_dir,
                    ddl_map,
                    evidence_map,
                    sql_salchemy,
                    llm_config,
                    few_shot_dataset_path
                )
                future_to_id[future] = item["question_id"]
            
            # 收集结果
            final_results = []
            for future in as_completed(future_to_id):
                question_id = future_to_id[future]
                try:
                    result = future.result()
                    if result is not None:
                        final_results.append(result)
                except Exception as e:
                    print(f"处理问题 {question_id} 时发生错误: {str(e)}")
                pbar.update(1)
            
            # 保存结果
            with open(output_dir / "final_validated_results.json", 'w', encoding='utf-8') as f:
                json.dump(final_results, f, ensure_ascii=False, indent=2)
            
            print(f"所有问题处理完成，结果已保存到 {output_dir / 'final_validated_results.json'}")
            print(f"成功处理的问题数量: {len(final_results)}/{total_questions}")

    


if __name__ == "__main__":
    main()