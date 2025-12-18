import json
import os
from utils.code_executor import CodeExecutor

def update_json_with_code_results(json_file_path, code_dir_path):
    """
    Reads a JSON file, executes Python code from corresponding files,
    updates the JSON with the code and its results, and saves the changes.

    Args:
        json_file_path (str): Path to the input JSON file.
        code_dir_path (str): Path to the directory containing Python code files.
    """
    # Initialize the CodeExecutor
    executor = CodeExecutor()

    try:
        # Load the JSON data
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Iterate through each item in the JSON data
        for entry in data:
            question_id = entry.get('question_id')
            if question_id is not None:
                code_file_name = f"{question_id}.txt"
                code_file_path = os.path.join(code_dir_path, code_file_name)

                if os.path.exists(code_file_path):
                    print(f"Processing question_id: {question_id} using {code_file_name}")
                    with open(code_file_path, 'r', encoding='utf-8') as f_code:
                        python_code = f_code.read()

                    # Execute the Python code
                    execution_result = executor.execute(python_code)

                    # Update the JSON entry
                    entry['related_python_code'] = python_code
                    if execution_result['error']:
                        entry['related_python_result'] = f"Error: {execution_result['error']['detailed']}"
                    else:
                        # Assuming the 'controlled_print' output or other direct print
                        # is what's desired for 'related_python_result'
                        entry['related_python_result'] = f"标准输出: {execution_result['stdout'].strip()}"
                        if execution_result['stderr']:
                            entry['related_python_result'] += f"\n标准错误: {execution_result['stderr'].strip()}"

                else:
                    print(f"No code file found for question_id: {question_id} at {code_file_path}")
            else:
                print("Entry missing 'question_id', skipping.")

        # Save the updated JSON data back to the file
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        print(f"Successfully updated and saved data to {json_file_path}")

    except FileNotFoundError:
        print(f"Error: JSON file not found at {json_file_path}")
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {json_file_path}. Check file format.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

# Define file paths
json_input_path = '/home/shenshuyu/SQL_tool/Output/generate_function_test/test_results_generate_function_full.json'
code_directory = '/home/shenshuyu/SQL_tool/Output/generate_function_test/rewrite'

# Run the update function
update_json_with_code_results(json_input_path, code_directory)