"""
Evidence过滤工具

在调用solve之前，使用LLM分析和过滤evidence，提取真正有用的信息，
特别是与table columns相关的内容。
"""

import autogen
from typing import Dict, Optional


def filter_and_combine_evidence(
    simplified_ddl: str,
    question: str,
    combine_evidence: str,
    llm_config: Dict
) -> str:
    """
    使用LLM分析和过滤evidence，提取真正有用的信息
    
    Args:
        simplified_ddl: 数据库结构（DDL）
        question: 要查询的问题
        combine_evidence: 原始的evidence列表（字符串）
        llm_config: LLM配置
        
    Returns:
        过滤和组合后的evidence字符串
    """
    if not combine_evidence or not combine_evidence.strip():
        return ""
    
    # 创建evidence分析agent
    evidence_analyzer = autogen.AssistantAgent(
        name="EvidenceAnalyzer",
        llm_config=llm_config,
        system_message="""You are an expert at analyzing database-related evidence and extracting useful information.
"""
    )
    
    user_proxy = autogen.UserProxyAgent(
        name="EvidenceUserProxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=0,
        code_execution_config=False
    )
    
    # 构建分析提示
    analysis_prompt = f"""我想请你帮忙分析一个SQL查询问题。这里有一些背景信息：

数据库结构：
{simplified_ddl}

我要查询的问题是：
{question}

另外，我这里有一个evidence列表，包含了很多数据库相关的知识：
{combine_evidence}

请你帮我：
1. 从evidence列表中找出真正有用的信息
4. 去除冗余、不相关的信息


请直接输出过滤后的evidence内容，不需要额外的解释或格式标记。如果evidence中没有有用信息，请输出空字符串。
输出格式类似下面这样（5行左右就可以了）：
"POPLATEK PO OBRATU" stands for issuance after transaction  
frequency = 'POPLATEK PO OBRATU' indicates client's statement issuance preference after transactions
"""
    
    try:
        user_proxy.initiate_chat(evidence_analyzer, message=analysis_prompt)
        filtered_evidence = user_proxy.last_message(evidence_analyzer)
        
        # 处理消息可能是字典的情况（提取content字段）
        if isinstance(filtered_evidence, dict):
            filtered_evidence = filtered_evidence.get("content", "")
        else:
            filtered_evidence = str(filtered_evidence) if filtered_evidence else ""
        
        # 清理输出（移除可能的markdown格式标记）
        if filtered_evidence:
            # 移除常见的markdown标记
            filtered_evidence = filtered_evidence.strip()
            if filtered_evidence.startswith("```"):
                # 移除代码块标记
                lines = filtered_evidence.split('\n')
                filtered_lines = []
                in_code_block = False
                for line in lines:
                    if line.strip().startswith("```"):
                        in_code_block = not in_code_block
                        continue
                    if not in_code_block:
                        filtered_lines.append(line)
                filtered_evidence = '\n'.join(filtered_lines).strip()
            
            return filtered_evidence
        else:
            return ""
    except Exception as e:
        print(f"[警告] Evidence过滤失败: {e}")
        # 如果LLM调用失败，返回原始evidence
        return combine_evidence

