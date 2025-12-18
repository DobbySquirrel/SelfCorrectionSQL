import json
import os
import glob
from pathlib import Path
import autogen
from typing import List, Dict, Any, Tuple, Optional
from utils.agent_helpers import AgentHelpers
import yaml
def load_config(config_path):
    """加载配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config

def setup_llm_config():
    """设置LLM配置"""
    config_path = Path(__file__).parent / "config" / "config.yaml"
    
    # 加载配置
    config = load_config(config_path)
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

def load_error_analysis(file_path: str) -> Dict[str, Any]:
    """加载error analysis文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误：找不到文件 {file_path}")
        raise
    except json.JSONDecodeError:
        print(f"错误：文件 {file_path} 不是有效的JSON格式")
        raise

def load_code_gen_result(file_path: str) -> Dict[str, Any]:
    """加载code generation结果文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误：找不到文件 {file_path}")
        raise
    except json.JSONDecodeError:
        print(f"错误：文件 {file_path} 不是有效的JSON格式")
        raise

def extract_python_result(code_gen_data: Dict[str, Any]) -> Optional[str]:
    """从code generation结果中提取Python执行结果"""
    if not code_gen_data.get("top3_code_records"):
        return None
    
    # 获取第一个记录的结果
    first_record = code_gen_data["top3_code_records"][0]
    if "result" in first_record:
        # 清理结果字符串
        result = first_record["result"]
        if result.startswith("标准输出: "):
            result = result.replace("标准输出: ", "").strip()
        return result
    return None

def create_comparison_agent():
    """创建用于比较结果的agent"""
    llm_config = setup_llm_config()
    
    comparison_agent = autogen.AssistantAgent(
        name="ComparisonAgent",
        llm_config=llm_config,
        system_message="""你是一个专门用于比较结果的专家。
你需要判断两个结果是否表达了相同的含义。
在比较时，你需要考虑问题的上下文和证据，确保结果在语义上是等价的。
即使格式可能不同，只要表达的实际含义相同，就应该判定为相同。
请使用XML格式返回判断结果和原因。"""
    )
    
    user_proxy = autogen.UserProxyAgent(
        name="user_proxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=0,
        code_execution_config=False
    )
    
    return comparison_agent, user_proxy

def compare_results(agent, user_proxy, result1: str, result2: str, question: str, evidence: str) -> Tuple[bool, str]:
    """使用agent比较两个结果是否表达相同含义"""
    prompt = f"""
问题背景：
问题：{question}
证据：{evidence}

请比较以下两个结果是否表达相同的含义：

结果1: {result1}
结果2: {result2}

请根据问题和证据的上下文，判断这两个结果是否表达相同的含义。
请使用以下XML格式回答：

```xml
<judge>
true/false
</judge>
<reason>
简要解释原因
</reason>
```
"""
    
    user_proxy.initiate_chat(agent, message=prompt)
    response = user_proxy.last_message(agent)
    judge = AgentHelpers.extract_xml_tag(response, "judge")
    reason = AgentHelpers.extract_xml_tag(response, "reason")
    
    # 将judge字符串转换为布尔值
    is_same = judge.lower().strip() == "true"
    
    return is_same, reason

def extract_file_id(file_path: str) -> Optional[int]:
    """从文件路径中提取ID"""
    try:
        return int(Path(file_path).stem.split("_")[-1])
    except (ValueError, IndexError):
        print(f"错误：无法从文件名 {file_path} 中提取有效的ID")
        return None

def get_result_value(result_list: List[Any]) -> Optional[str]:
    """安全地获取结果列表中的第一个值"""
    if result_list and len(result_list) > 0:
        return str(result_list[0])
    return None

def main():
    # 设置路径
    base_dir = Path("/home/shenshuyu/SQL_tool")
    code_gen_dir = base_dir / "generated_code_cache"
    error_analysis_path = base_dir / "score_caluation/error_analysis_straightforward.json"
    
    if not code_gen_dir.exists():
        print(f"错误：目录 {code_gen_dir} 不存在")
        return
    
    # 加载error analysis数据
    try:
        error_analysis = load_error_analysis(error_analysis_path)
    except Exception as e:
        print(f"加载error analysis文件时发生错误: {str(e)}")
        return
    
    # 创建比较agent
    comparison_agent, user_proxy = create_comparison_agent()
    
    # 遍历所有code_gen_result文件
    results = []
    processed_files = 0
    error_files = 0
    
    for code_gen_file in glob.glob(str(code_gen_dir / "code_gen_result_*.json")):
        try:
            # 从文件名获取ID
            file_id = extract_file_id(code_gen_file)
            if file_id is None:
                error_files += 1
                continue
            
            # 加载code generation结果
            code_gen_data = load_code_gen_result(code_gen_file)
            python_result = extract_python_result(code_gen_data)
            
            if python_result is None:
                error_files += 1
                continue
                
            # 获取对应的predicted_res和ground_truth_res
            error_analysis_entry = next(
                (entry for entry in error_analysis['error_details'] 
                 if entry.get('idx') == str(file_id)),
                None
            )
            if not error_analysis_entry:
                error_files += 1
                continue
                
            predicted_res = error_analysis_entry.get("predicted_res", [])
            ground_truth_res = error_analysis_entry.get("ground_truth_res", [])
            question = error_analysis_entry.get("question", "")
            evidence = error_analysis_entry.get("evidence", "")
            
            predicted_value = get_result_value(predicted_res)
            ground_truth_value = get_result_value(ground_truth_res)
            
            if not predicted_value or not ground_truth_value:
                error_files += 1
                continue
            
            # 比较结果
            matches_predicted, predicted_reason = compare_results(
                comparison_agent, 
                user_proxy, 
                python_result, 
                predicted_value,
                question,
                evidence
            )
            
            matches_ground_truth, ground_truth_reason = compare_results(
                comparison_agent, 
                user_proxy, 
                python_result, 
                ground_truth_value,
                question,
                evidence
            )
            
            results.append({
                "id": file_id,
                "question": question,
                "evidence": evidence,
                "python_result": python_result,
                "predicted_res": predicted_value,
                "ground_truth_res": ground_truth_value,
                "matches_predicted": matches_predicted,
                "matches_ground_truth": matches_ground_truth,
                "predicted_reason": predicted_reason,
                "ground_truth_reason": ground_truth_reason
            })
            
            processed_files += 1
            
        except Exception as e:
            print(f"处理文件 {code_gen_file} 时发生错误: {str(e)}")
            error_files += 1
    
    # 保存分析结果
    output_path = base_dir / "result_analysis.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    # 打印统计信息
    total = len(results)
    matches_predicted_count = sum(1 for r in results if r["matches_predicted"])
    matches_ground_truth_count = sum(1 for r in results if r["matches_ground_truth"])
    
    print(f"\n分析结果统计:")
    print(f"总共处理的文件数: {processed_files + error_files}")
    print(f"成功处理的文件数: {processed_files}")
    print(f"处理失败的文件数: {error_files}")
    print(f"与predicted_res匹配的数量: {matches_predicted_count} ({matches_predicted_count/total*100:.2f}%)")
    print(f"与ground_truth_res匹配的数量: {matches_ground_truth_count} ({matches_ground_truth_count/total*100:.2f}%)")
    print(f"\n详细结果已保存到: {output_path}")

if __name__ == "__main__":
    main()