import os
import subprocess
import logging
from pathlib import Path
from datasets import load_dataset
import json
import argparse  # <-- Add argparse import

# Import SDTAgent from your agent module
from agent.main_agent import SDTAgent, WorkflowState, ProjectContext
from helpers import extract_repo_name_from_url
from .logging_config import logger


def main(num_samples):
    # --- Load SWE-bench dataset ---
    dataset = load_dataset("princeton-nlp/SWE-bench_Lite", split="dev")
    dataset = dataset.select(range(num_samples))  # Use user-specified number of samples

    # Log dataset info and sample attributes
    logger.info(f"Loaded dataset with {len(dataset)} instances.")
    if len(dataset) > 0:
        logger.info(f"Sample instance keys: {list(dataset[0].keys())}")
        logger.info(f"Sample instance: {dataset[0]}")
        # No longer warn about 'issue_id', use 'instance_id' everywhere

    # --- Initialize SDTAgent ---
    agent = SDTAgent()

    # --- Run the benchmark ---
    results = []
    for instance in dataset:
        try:
            logger.info(f"Processing {instance.get('repo', '')}...")
            if "instance_id" not in instance:
                logger.error(
                    f"Skipping {instance.get('repo', '')}: missing 'instance_id'"
                )
                continue
            if "repo" not in instance:
                logger.error(f"Skipping instance: missing 'repo'")
                continue
            if "problem_statement" not in instance or "base_commit" not in instance:
                logger.error(
                    f"Skipping {instance.get('repo', '')}: missing required fields"
                )
                continue
            # --- Use SDTAgent.run_workflow with WorkflowState from instance ---
            state = instance_to_state(instance)
            final_state = agent.run_workflow(state, include_commit_and_pr=False)
            # Collect results
            results.append(
                {
                    "repo": instance["repo"],
                    "instance_id": instance["instance_id"],
                    "tests_passed": getattr(final_state, "passed", None),
                    "code_changes": getattr(final_state, "code_changes", None),
                    "test_results": getattr(final_state, "test_results", None),
                    "lint_results": getattr(final_state, "lint_results", None),
                }
            )
        except Exception as e:
            logger.error(f"Error processing {instance.get('repo', '')}: {e}")

    # --- Output the results ---
    for result in results:
        repo_path = (
            result["repo"].replace("https://github.com/", "").replace(".git", "")
        )
        issue_url = (
            f"https://github.com/{repo_path}/issues/{result['instance_id']}"
            if result.get("instance_id")
            else "N/A"
        )
        logger.info(f"Results for {result['repo']} - {issue_url}:")
        logger.info(f"Tests passed: {result['tests_passed']}")
        logger.info(f"Code changes: {result.get('code_changes', '')}")
        logger.info(f"Test results: {result.get('test_results', '')}")
        logger.info(f"Lint results: {result.get('lint_results', '')}")


def instance_to_state(instance):
    """
    Convert a SWE-bench instance to WorkflowState and ProjectContext.
    Only use fields supported by SDT_Buddy_Class models.
    """
    repo_url = instance["repo"]
    # Only prepend if not already a full URL
    if not repo_url.startswith("http"):
        if not repo_url.endswith(".git"):
            repo_url = f"https://github.com/{repo_url}.git"
        else:
            repo_url = f"https://github.com/{repo_url}"
    tech_stack = "Python"  # Default; could be improved by heuristics
    dependencies = ["pytest", "ruff", "black"]

    # --- Use FAIL_TO_PASS and PASS_TO_PASS for relevant_files if present ---
    relevant_files = []
    for key in ["FAIL_TO_PASS", "PASS_TO_PASS"]:
        if key in instance and instance[key]:
            try:
                files = json.loads(instance[key])
                if isinstance(files, list):
                    relevant_files.extend(files)
            except Exception:
                pass  # Ignore parse errors, fallback to empty

    context = ProjectContext(
        repo_link=repo_url,
        project_description=f"SWE-bench instance {instance.get('instance_id', '')}",
        language=tech_stack,
        relevant_files=relevant_files,  # Use extracted relevant files
        dependencies=dependencies,
    )
    state = WorkflowState(
        github_issue=instance.get("problem_statement", ""),
        project_context=context,
        plan=None,
        code_changes=None,
        test_results=None,
    )
    return state


def benchmark_agent_on_instance(instance):
    repo_url = instance["repo"]
    instance_id = instance["instance_id"]
    problem_statement = instance["problem_statement"]
    base_commit = instance["base_commit"]

    # --- Fix: Ensure repo_url is a valid git URL ---
    # Only prepend if not already a full URL
    if not repo_url.startswith("http"):
        if not repo_url.endswith(".git"):
            repo_url = f"https://github.com/{repo_url}.git"
        else:
            repo_url = f"https://github.com/{repo_url}"
    # Parse owner/repo for issue URL
    repo_path = repo_url.replace("https://github.com/", "").replace(".git", "")
    issue_url = (
        f"https://github.com/{repo_path}/issues/{instance_id}" if instance_id else "N/A"
    )

    # Clone the repository at the specified commit
    repo_name = extract_repo_name_from_url(repo_url)
    repo_dir = Path(f"./GitHubIssue/{repo_name}")
    if not repo_dir.exists():
        subprocess.run(["git", "clone", repo_url, str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "checkout", base_commit], check=True)

    # Generate the patch using SDTAgent
    prompt = f"Fix the following issue in the codebase:\n{problem_statement}"
    patch = agent.agent_respond(prompt)

    # Warn if patch does not look like a diff
    if not patch.lstrip().startswith("diff "):
        logger.warning(
            "Agent did not return a unified diff. Patch application will likely fail."
        )

    # Apply the patch to the repository
    patch_file = repo_dir / "patch.diff"
    with open(patch_file, "w", encoding="utf-8") as f:
        f.write(patch)
    patch_proc = subprocess.run(
        ["git", "apply", str(patch_file)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if patch_proc.returncode != 0:
        logger.error(
            f"Patch application failed:\nSTDOUT:\n{patch_proc.stdout}\nSTDERR:\n{patch_proc.stderr}"
        )
    else:
        logger.info("Patch applied successfully.")

    # Run tests to verify the patch
    result = subprocess.run(
        ["pytest", "--maxfail=1", "--disable-warnings", "-q"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    tests_passed = result.returncode == 0

    # Log the results and test output
    logger.info(f"Repo: {repo_url}")
    logger.info(f"Issue: {issue_url}")
    logger.info(f"Tests passed: {tests_passed}")
    logger.info(f"Patch:\n{patch}")
    logger.info(f"Pytest STDOUT:\n{result.stdout}")
    logger.info(f"Pytest STDERR:\n{result.stderr}")

    return {
        "repo": repo_url,
        "instance_id": instance_id,
        "tests_passed": tests_passed,
        "patch": patch,
        "pytest_stdout": result.stdout,
        "pytest_stderr": result.stderr,
        "patch_apply_stdout": patch_proc.stdout,
        "patch_apply_stderr": patch_proc.stderr,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Benchmark SDTAgent on SWE-bench dataset."
    )
    parser.add_argument(
        "-n",
        "--num-samples",
        type=int,
        default=3,
        help="Number of samples to run (default: 3)",
    )
    args = parser.parse_args()

    main(args.num_samples)
else:
    # For backward compatibility if imported as a module
    main(3)
