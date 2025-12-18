import json
import os
import sys
from pathlib import Path
from tqdm import tqdm
import argparse

# Add project root directory to system path
sys.path.append(str(Path(__file__).parent.parent))

from agents.autogen_agents import AgentSystem
from core.database_connector import DatabaseConnector
from core.reasoning_tree import ReasoningTree
from test_agents import load_dataset, load_config, setup_llm_config

def main(run_full_dataset=True):
    # Load configuration file
    config_path = Path(__file__).parent / "config" / "config.yaml"
    config = load_config(config_path)

    # Set output folder
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/a_star")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_ongoing_file = output_dir / "test_results_a_star_ongoing.json"
    output_full_file = output_dir / "test_results_a_star_full.json"

    # Load dataset
    dataset_path = "/home/shenshuyu/SQL_tool/data/subset_ppl_dev_python.json"
    dataset = load_dataset(dataset_path)

    # Load original dataset to get correct question_id
    original_dataset_path = "/home/shenshuyu/SQL_tool/data/sub_sampled_bird_dev_set.json"
    original_dataset = load_dataset(original_dataset_path)

    # If only running the first 5 samples
    if not run_full_dataset:
        dataset = dataset[:5]
        original_dataset = original_dataset[:5]
        print("Only running the first 5 dataset samples")
    else:
        print(f"Running full dataset ({len(dataset)} samples)")

    # Load pre-generated SQL results
    sql_pandas_path = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_pandas.json"
    sql_salchemy_path = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_sqlalchemy.json"

    sql_pandas = {}
    sql_salchemy = {}
    try:
        with open(sql_pandas_path, 'r', encoding='utf-8') as f:
            sql_pandas = json.load(f)
    except Exception as e:
        print(f"Error loading SQL Pandas file: {e}")

    try:
        with open(sql_salchemy_path, 'r', encoding='utf-8') as f:
            sql_salchemy = json.load(f)
    except Exception as e:
        print(f"Error loading SQL SQLAlchemy file: {e}")

    # Ensure both datasets have the same length
    assert len(dataset) == len(original_dataset), "Dataset lengths do not match"

    # Set LLM configuration
    llm_config = setup_llm_config(config)

    # Create result storage list
    results = []
    # This set will store question_ids that have been successfully solved in previous runs
    solved_question_ids = set()

    # Load existing results if any to resume
    if output_ongoing_file.exists():
        try:
            with open(output_ongoing_file, 'r', encoding='utf-8') as f:
                existing_results = json.load(f)
                for r in existing_results:
                    results.append(r) # Add all existing results to the results list
                    if r.get("is_solved") == True: # Check the 'is_solved' field
                        solved_question_ids.add(r["question_id"])
            print(f"Resuming. Found {len(existing_results)} existing results, {len(solved_question_ids)} of which are solved.")
        except json.JSONDecodeError as e:
            print(f"Error decoding ongoing results file, starting fresh: {e}")
        except Exception as e:
            print(f"An unexpected error occurred while loading ongoing results, starting fresh: {e}")

    # Iterate through all questions in the dataset
    for i, (item, original_item) in enumerate(tqdm(zip(dataset, original_dataset), desc="Processing questions", total=len(dataset))):
        index = original_item["question_id"]

        # Skip if already solved
        if index in solved_question_ids:
            # print(f"Skipping question {i+1}/{len(dataset)} (ID: {index}) - already solved.")
            continue

        db_name = item["db"]
        question = item["question"]
        schema_info = item["simplified_ddl"]
        foreign_key = item["foreign_key"]
        evidence = item["evidence"]
        tables_schema_first_three = item["ddl_data"]
        example_data = item["example"]

        print(f"\nProcessing question {i+1}/{len(dataset)}: {question} (ID: {index})")

        db_connector = DatabaseConnector(db_name)
        reasoning_tree = ReasoningTree(question)
        agent_system = AgentSystem(llm_config, db_connector)

        try:
            # Use A* workflow
            agent_system._solve_a_star(
                reasoning_tree.root,
                reasoning_tree,
                sql_pandas=sql_pandas.get(str(index), ""),
                sql_salchemy=sql_salchemy.get(str(index), ""),
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
            if evidences and evidences[0]: # Ensure evidences is not empty and the first element is not None/empty
                formatted_sql = " ".join(evidences[0].strip().split())
                query_result, error_message = db_connector.execute_query(formatted_sql)
                if query_result is not None:
                    sql_result = query_result.to_dict(orient='records')
                else:
                    sql_error = error_message

            # Store result
            results.append({
                "question_id": index,
                "db": db_name,
                "question": question,
                "is_solved": is_solved,
                "python_code": action,
                "python_result": cell_value,
                "sql": evidences[0] if evidences else None,
                "sql_result": sql_result,
                "sql_error": sql_error
            })
            # Only add to solved_question_ids if the question was actually solved in this run
            if is_solved:
                solved_question_ids.add(index)

            # Save intermediate results every 5 questions or at the end of the dataset
            if (i + 1) % 5 == 0 or (i + 1) == len(dataset):
                with open(output_ongoing_file, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=4)
                print(f"Saved intermediate results (Completed {len(solved_question_ids)} solved questions so far)")

        except Exception as e:
            print(f"Error processing question {index}: {e}")
            results.append({
                "question_id": index,
                "db": db_name,
                "question": question,
                "is_solved": False, # Mark as False if an error occurred
                "python_code": None,
                "python_result": None,
                "error": str(e),
                "sql": None,
                "sql_result": [],
                "sql_error": None
            })
            # Questions with errors will not be added to solved_question_ids,
            # allowing them to be retried in subsequent runs.

            with open(output_ongoing_file, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=4)

        finally:
            # Ensure database connection is closed for each question if it was opened
            if db_connector:
                db_connector.disconnect()

    # Save final results
    with open(output_full_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

    # Print statistics
    solved_count = sum(1 for r in results if r.get("is_solved", False))
    total_count = len(results)
    print(f"\nTesting complete! Successfully solved: {solved_count}/{total_count} ({solved_count/total_count*100:.2f}%)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Test agent system with A* workflow')
    parser.add_argument('--sample', action='store_true', help='Only run the first 5 samples')
    args = parser.parse_args()

    main(run_full_dataset=not args.sample)