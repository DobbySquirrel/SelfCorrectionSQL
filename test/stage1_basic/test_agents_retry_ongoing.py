import json
import os
import sys
import yaml
from pathlib import Path
from tqdm import tqdm
import pandas as pd
import argparse

# Add project root directory to system path
sys.path.append(str(Path(__file__).parent.parent))

from agents.autogen_agents import AgentSystem
from core.database_connector import DatabaseConnector
from core.reasoning_tree import ReasoningTree

def load_dataset(file_path):
    """Load dataset"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data

def load_config(config_path):
    """Load configuration file"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config

def setup_llm_config(config):
    """Set up LLM configuration based on the config file"""
    llm_config = {
        "config_list": [
            {
                "model": config.get("model", "gpt-4o-mini"),
                "api_key": config.get("api", ""),
                "base_url": config.get("base_url", "https://api.chsdw.top/v1/chat/completions")
            }
        ],
        "temperature": 0.7,
    }
    return llm_config

def main():
    # Load configuration file
    config_path = Path(__file__).parent / "config" / "config.yaml"
    config = load_config(config_path)
    
    # Set output folder
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/5_23")
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Define paths - Corrected to use Path objects
    results_ongoing_path = output_dir / "test_results_straightforward_ongoing.json"
    dataset_path = Path("/home/shenshuyu/SQL_tool/data/subset_ppl_dev_python.json") # Convert to Path
    original_dataset_path = Path("/home/shenshuyu/SQL_tool/data/sub_sampled_bird_dev_set.json") # Convert to Path
    sql_pandas_path = Path("/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_pandas.json") # Convert to Path
    sql_salchemy_path = Path("/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_sqlalchemy.json") # Convert to Path

    # Load original results file (if it exists)
    existing_results = []
    if results_ongoing_path.exists():
        existing_results = load_dataset(results_ongoing_path)
        print(f"Loaded {len(existing_results)} existing results from {results_ongoing_path}")
    
    # Load original dataset to get all question_ids
    dataset = load_dataset(dataset_path)
    original_dataset = load_dataset(original_dataset_path)
    
    # Create question_id to dataset index mapping for the *original* dataset
    original_id_to_index = {item["question_id"]: i for i, item in enumerate(original_dataset)}
    
    # Create a set of all question_ids from the original dataset that *should* be processed
    all_question_ids_to_process = set(original_id_to_index.keys())

    # Get question_ids that have already been processed and saved
    processed_ids = {r["question_id"] for r in existing_results}
    
    # Identify failed IDs from existing results
    failed_ids = {r["question_id"] for r in existing_results if not r["is_solved"]}
    print(f"Found {len(failed_ids)} failed cases from existing results.")
    
    # Identify missing IDs (those in original dataset but not in processed_ids)
    missing_ids = all_question_ids_to_process - processed_ids
    print(f"Found {len(missing_ids)} missing cases from the dataset.")

    # Combine failed and missing IDs into a unique list for reprocessing/initial processing
    ids_to_run = sorted(list(failed_ids.union(missing_ids)))
    
    if not ids_to_run:
        print("No failed or missing cases to run. All questions processed or solved.")
        return
    
    print(f"Total {len(ids_to_run)} cases to process/reprocess.")
    
    # Load pre-generated SQL results
    sql_pandas = {}
    if sql_pandas_path.exists():
        try:
            with open(sql_pandas_path, 'r', encoding='utf-8') as f:
                sql_pandas = json.load(f)
        except Exception as e:
            print(f"Error loading SQL Pandas file: {e}")
            
    sql_salchemy = {}
    if sql_salchemy_path.exists():
        try:
            with open(sql_salchemy_path, 'r', encoding='utf-8') as f:
                sql_salchemy = json.load(f)
        except Exception as e:
            print(f"Error loading SQL SQLAlchemy file: {e}")
    
    # Set LLM configuration
    llm_config = setup_llm_config(config)
    
    # Create a dictionary to store all results, merging existing and new ones
    all_results_dict = {r["question_id"]: r for r in existing_results}
    
    # Iterate through the combined list of IDs to run
    for i, question_id in enumerate(tqdm(ids_to_run, desc="Processing questions")):
        if question_id not in original_id_to_index:
            print(f"Warning: question_id {question_id} not found in original dataset, skipping.")
            continue
            
        # Get the corresponding index from the original dataset
        idx = original_id_to_index[question_id]
        
        # Ensure 'idx' is within the bounds of 'dataset'
        if idx >= len(dataset):
            print(f"Warning: Index {idx} for question_id {question_id} is out of bounds for 'subset_ppl_dev_python.json', skipping.")
            continue
            
        item = dataset[idx]
        original_item = original_dataset[idx] # Use original_item for question_id consistently
        
        db_name = item["db"]
        question = item["question"]
        schema_info = item["simplified_ddl"]
        foreign_key = item["foreign_key"]
        evidence = item["evidence"]
        current_question_id = original_item["question_id"] # Use this as the ID for the result
        tables_schema_first_three = item["ddl_data"]
        example_data = item["example"]
        
        print(f"\nProcessing question {i+1}/{len(ids_to_run)}: {question} (ID: {current_question_id})")

        db_path = db_name
        db_connector = DatabaseConnector(db_path)
        
        # Create reasoning tree
        reasoning_tree = ReasoningTree(question)
        
        # Create Agent system
        agent_system = AgentSystem(llm_config, db_connector)
        
        try:
            # Call _solve_straightforward method
            agent_system._solve_straightforward(
                reasoning_tree.root, 
                reasoning_tree,
                sql_pandas=sql_pandas.get(str(current_question_id), ""),
                sql_salchemy=sql_salchemy.get(str(current_question_id), ""),
                db_name=db_name,
                tables_schema_first_three=tables_schema_first_three,
                schema_info="db_name:"+db_name+"\n"+schema_info+"\nforeign_key:"+foreign_key,
                additional_context=evidence,
                example_data=example_data
            )
            
            # Get results
            is_solved = reasoning_tree.root.is_solved
            cell_value = reasoning_tree.root.cell_value
            evidences = reasoning_tree.root.evidences
            action = reasoning_tree.root.actions
            
            # Execute SQL query and get result
            sql_result = []
            sql_error = None
            if evidences and evidences[0]: # Check if evidences exists and is not empty
                formatted_sql = " ".join(evidences[0].strip().split())
                query_result, error_message = db_connector.execute_query(formatted_sql)
                
                # Convert DataFrame to list of dictionaries
                if query_result is not None:
                    sql_result = query_result.to_dict(orient='records')
                else:
                    sql_error = error_message
            
            # Store result in the dictionary
            all_results_dict[current_question_id] = {
                "question_id": current_question_id,
                "db": db_name,
                "question": question,
                "is_solved": is_solved,
                "python_code": action,
                "python_result": cell_value,
                "sql": evidences[0] if evidences else None,
                "sql_result": sql_result,
                "sql_error": sql_error
            }
            
            print(f"Question solved: {is_solved}")
            print(f"Result: {sql_result[:200]}..." if len(sql_result) > 200 else f"Result: {sql_result}")
            print(f"SQL: {evidences[0] if evidences else None}")
            
            # Save intermediate results every 5 questions or at the end
            if (i + 1) % 5 == 0 or (i + 1) == len(ids_to_run):
                with open(output_dir / "test_results_straightforward_ongoing.json", "w", encoding="utf-8") as f:
                    json.dump(list(all_results_dict.values()), f, ensure_ascii=False, indent=4)
                print(f"Saved intermediate results to {output_dir}/test_results_straightforward_ongoing.json (Completed {i+1}/{len(ids_to_run)} questions)")
            
        except Exception as e:
            print(f"Error processing question {current_question_id}: {e}")
            all_results_dict[current_question_id] = {
                "question_id": current_question_id,
                "db": db_name,
                "question": question,
                "is_solved": False,
                "python_code": None,
                "python_result": None,
                "error": str(e),
                "sql": None,
                "sql_result": [],
                "sql_error": None
            }
            
            # Save intermediate results on error
            with open(output_dir / "test_results_straightforward_ongoing.json", "w", encoding="utf-8") as f:
                json.dump(list(all_results_dict.values()), f, ensure_ascii=False, indent=4)
            print(f"Error processing question {i+1}, saved intermediate results.")
        
        # Disconnect from database after each question to avoid too many open connections
        db_connector.disconnect()

    # Convert dictionary back to a list for final save
    final_results = list(all_results_dict.values())
    
    # Save final results to a new file
    with open(output_dir / "test_results_straightforward_final.json", "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=4)
    print(f"Final results saved to {output_dir}/test_results_straightforward_final.json")
    
    # Print statistics
    solved_count = sum(1 for r in final_results if r["is_solved"])
    total_count = len(final_results)
    print(f"\nProcessing complete! Successfully solved: {solved_count}/{total_count} ({solved_count/total_count*100:.2f}%)")

if __name__ == "__main__":
    main()