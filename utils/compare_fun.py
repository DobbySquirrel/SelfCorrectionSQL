def compare_results(sql_result, python_result: any) -> bool:
    """
    比较 SQL 执行结果与 Python 原子操作链的结果。对于多行结果，
    使用浮点容差逐行比对，不依赖顺序，并详细打印差异。

    返回:
        bool: 如果结果匹配，则为 True，否则为 False。
    """
    import math
    import pandas as pd
    import numpy as np

    print("\n--- 比较结果 ---")

    # 打印部分 SQL 和 Python 结果预览
    preview_n = 5
    print(f"SQL 结果预览（前{preview_n}行）:")
    for row in list(sql_result)[:preview_n]:
        print(row)
    print(f"Python 链结果预览（前{preview_n}行）:")
    try:
        for row in list(python_result)[:preview_n]:
            print(row)
    except TypeError:
        print(python_result)

    if not sql_result:
        # 检查Python结果是否也为空
        try:
            python_empty = not python_result or (hasattr(python_result, '__len__') and len(python_result) == 0)
        except (TypeError, AttributeError):
            # 如果python_result不支持len()或为空值，认为它是空的
            python_empty = not python_result
            
        if python_empty:
            print("结果 **匹配** SQL 和 Python 结果都为空")
            return True
        else:
            print("SQL 查询未返回有效结果进行比较（空结果），但 Python 结果不为空")
            return False

    # 用于带浮点容差的逐元素比较的辅助函数
    def are_elements_close(a, b, rel_tol=1e-9, abs_tol=0.0):
        if isinstance(a, (int, float, np.integer, np.floating)) and isinstance(b, (int, float, np.integer, np.floating)):
            if pd.isna(a) and pd.isna(b):
                return True
            if pd.isna(a) or pd.isna(b):
                return False
            return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)
        elif pd.isna(a) and pd.isna(b):
            return True
        return a == b

    # 标准化结果格式：将列表转换为元组
    if isinstance(sql_result, list):
        sql_result = tuple(sql_result)

    # 处理标量结果的情况
    # 检查SQL结果是否为单元素元组
    if len(sql_result) == 1:
        sql_first = sql_result[0]
        # 如果SQL第一个元素是元组且只有一个元素，或者直接是标量
        if isinstance(sql_first, tuple) and len(sql_first) == 1:
            sql_comparable_result = sql_first[0]
        elif not isinstance(sql_first, tuple):
            sql_comparable_result = sql_first
        else:
            # 多列结果，需要进一步处理
            sql_comparable_result = None
            
        # 处理Python结果：如果是单元素元组，提取标量值
        # 处理Python结果：递归提取标量值
        python_comparable_result = python_result
        while isinstance(python_comparable_result, tuple) and len(python_comparable_result) == 1:
            python_comparable_result = python_comparable_result[0]
            
        if sql_comparable_result is not None:
            print(f"SQL 结果 (标量): {sql_comparable_result}")
            print(f"Python 链结果 (标量): {python_comparable_result}")
            if are_elements_close(sql_comparable_result, python_comparable_result):
                print("结果 **匹配**（在浮点容差范围内）！")
                return True
            else:
                print("结果 **不匹配**。")
                return False
    
    # 处理多行结果（元组的元组）
    # 确保python_result是可迭代的
    try:
        python_result_list = list(python_result)
    except TypeError:
        print(f"Python结果不是可迭代对象，无法与多行SQL结果比较")
        return False
        
    if len(sql_result) != len(python_result_list):
        print(f"行数不一致：SQL {len(sql_result)} 行，Python {len(python_result_list)} 行")
        return False

    matched = [False] * len(python_result_list)
    unmatched_sql = []
    for sql_row in sql_result:
        found = False
        for i, py_row in enumerate(python_result_list):
            if not matched[i] and all(are_elements_close(a, b) for a, b in zip(sql_row, py_row)):
                matched[i] = True
                found = True
                break
        if not found:
            unmatched_sql.append(sql_row)

    unmatched_python = [py_row for i, py_row in enumerate(python_result_list) if not matched[i]]

    if unmatched_sql or unmatched_python:
        print("结果 **不匹配**（内容或顺序不同）。")
        if unmatched_sql:
            print("--- SQL中未能匹配到的行 ---")
            for row in unmatched_sql:
                print(row)
        if unmatched_python:
            print("--- Python中未能匹配到的行 ---")
            for row in unmatched_python:
                print(row)
        print("--- 差异详情结束 ---")
        return False

    print("结果 **匹配**（浮点容差）！")
    return True