import ast
import re
from collections import OrderedDict
import os

# 1. 读取 stat_atomic_operators.py 的 node_to_func
stat_atomic_path = os.path.join('utils', 'stat_atomic_operators.py')
with open(stat_atomic_path, 'r', encoding='utf-8') as f:
    content = f.read()
node_to_func_match = re.search(r'node_to_func\s*=\s*{([\s\S]*?)}', content)
if not node_to_func_match:
    raise ValueError('未找到 node_to_func 字典')
node_to_func_body = node_to_func_match.group(1)
node_func_pairs = re.findall(r"'([\w]+)'\s*:\s*'([\w_]+)\((.*?)\)'", node_to_func_body)
node_to_func = OrderedDict()
for key, func, _ in node_func_pairs:
    node_to_func[key] = func

# 2. 读取 sql_atomic_operators.py，提取所有 op_xxx/build_xxx 函数签名
sql_atomic_path = os.path.join('utils', 'sql_atomic_operators.py')
with open(sql_atomic_path, 'r', encoding='utf-8') as f:
    sql_atomic_code = f.read()
parsed = ast.parse(sql_atomic_code)

# 4. 提取所有 op_xxx/build_xxx 函数的签名（含类型、默认值、*args, **kwargs）
def get_arg_str(arg, default, annotation):
    arg_str = arg.arg
    if annotation is not None:
        try:
            arg_str += ': ' + ast.unparse(annotation)
        except Exception:
            pass
    if default is not None:
        try:
            val = ast.literal_eval(default)
            if isinstance(val, str):
                arg_str += f" = '{val}'"
            else:
                arg_str += f" = {val}"
        except Exception:
            try:
                arg_str += f" = {ast.unparse(default)}"
            except Exception:
                arg_str += f" = <default>"
    return arg_str

def get_return_type(node):
    """提取函数的返回类型"""
    if node.returns is not None:
        try:
            return ast.unparse(node.returns)
        except Exception:
            return "Any"
    return "Any"

func_sigs = {}
func_return_types = {}
for node in parsed.body:
    if isinstance(node, ast.FunctionDef):
        func_name = node.name
        if func_name in set(node_to_func.values()) or func_name == 'op_is_not_null' or func_name == 'op_null_if':
            params = []
            # 普通参数
            total_args = node.args.args
            total_defaults = node.args.defaults
            total_annotations = [a.annotation if hasattr(a, 'annotation') else None for a in total_args]
            num_required = len(total_args) - len(total_defaults)
            for i, arg in enumerate(total_args):
                if arg.arg == 'self':
                    continue
                annotation = arg.annotation if hasattr(arg, 'annotation') else None
                default = None
                if i >= num_required:
                    default = total_defaults[i - num_required]
                params.append(get_arg_str(arg, default, annotation))
            # *args
            if node.args.vararg:
                vararg = node.args.vararg
                vararg_str = '*' + vararg.arg
                if vararg.annotation:
                    try:
                        vararg_str += ': ' + ast.unparse(vararg.annotation)
                    except Exception:
                        pass
                params.append(vararg_str)
            # 关键字only参数（*args后面的参数）
            kwonlyargs = node.args.kwonlyargs
            kw_defaults = node.args.kw_defaults
            for i, arg in enumerate(kwonlyargs):
                annotation = arg.annotation if hasattr(arg, 'annotation') else None
                default = kw_defaults[i] if i < len(kw_defaults) else None
                params.append(get_arg_str(arg, default, annotation))
            # **kwargs
            if node.args.kwarg:
                kwarg = node.args.kwarg
                kwarg_str = '**' + kwarg.arg
                if kwarg.annotation:
                    try:
                        kwarg_str += ': ' + ast.unparse(kwarg.annotation)
                    except Exception:
                        pass
                params.append(kwarg_str)
            sig = ', '.join(params)
            func_sigs[func_name] = sig
            func_return_types[func_name] = get_return_type(node)

# 5. 生成新的 node_to_func
new_node_to_func = OrderedDict()
for key, func in node_to_func.items():
    if key == 'is':
        # 特殊处理 'is'，用 | 连接两个函数签名
        sig1 = func_sigs.get('op_is_null', None)
        sig2 = func_sigs.get('op_is_not_null', None)
        return_type1 = func_return_types.get('op_is_null', 'Union[str, pd.Series]')
        return_type2 = func_return_types.get('op_is_not_null', 'Union[str, pd.Series]')
        part1 = f"op_is_null({sig1}) -> {return_type1}" if sig1 else f"op_is_null() -> {return_type1}"
        part2 = f"op_is_not_null({sig2}) -> {return_type2}" if sig2 else f"op_is_not_null() -> {return_type2}"
        new_node_to_func[key] = f"{part1}|{part2}"
    elif key == 'div':
        # 特殊处理 'div'，用 | 连接 op_null_if 和 op_div 的签名
        sig1 = func_sigs.get('op_null_if', None)
        sig2 = func_sigs.get('op_div', None)
        return_type1 = func_return_types.get('op_null_if', 'Union[str, pd.Series]')
        return_type2 = func_return_types.get('op_div', 'Union[str, pd.Series]')
        part1 = f"op_null_if({sig1}) -> {return_type1}" if sig1 else f"op_null_if() -> {return_type1}"
        part2 = f"op_div({sig2}) -> {return_type2}" if sig2 else f"op_div() -> {return_type2}"
        new_node_to_func[key] = f"{part1}|{part2}"
    else:
        real_func = func
        sig = func_sigs.get(real_func, None)
        return_type = func_return_types.get(real_func, 'Union[str, pd.Series]')
        if sig is not None:
            new_node_to_func[key] = f"{func}({sig}) -> {return_type}"
        else:
            new_node_to_func[key] = f"{func}() -> {return_type}"  # 没找到定义，保留空参数

# 6. 输出到 auto_stat_atomic_operators.py
with open('utils/auto_stat_atomic_operators.py', 'w', encoding='utf-8') as fout:
    fout.write('node_to_func = {\n')
    for k, v in new_node_to_func.items():
        fout.write(f'    \"{k}\": \"{v}\",\n')
    fout.write('}\n')

print('已生成 auto_stat_atomic_operators.py')