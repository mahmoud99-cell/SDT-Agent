from ..sdt_types import WorkflowState
import os
from ..logging_config import logger
from pathlib import Path
import re
import json
from ..helpers import (
    get_test_dir,
    extract_repo_name_from_url
)
from ..sdt_types import PlanModel


class PlanningNode:
    def __init__(self, agent_respond):
        self.agent_respond = agent_respond

    def run(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting planning phase " + "=" * 20)
        # Extract the issue text from the state
        issue_text = str(state.github_issue) or ""
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            else "https://github.com/SDT-DeveloperTwin/SDT-Testing-Project.git"
        )
        repo_name = extract_repo_name_from_url(repo_url)
        project_path = os.path.join("GitHubIssue", repo_name)

        # Check if the project path exists
        if not os.path.isdir(project_path):
            raise FileNotFoundError(
                f"Repository path '{project_path}' not found; did issue_analysis run?"
            )
        if not issue_text:
            raise ValueError("github_issue is empty")

        # --- 1. Extract all file names mentioned in the issue text ---
        # Use regex to find all file names with relevant extensions in the issue text
        file_name_pattern = r"[a-zA-Z0-9_\-/\\]+?\.(?:py|js|ts|tsx|jsx|json)"
        mentioned_files = {
            m.replace("\\", "/") for m in re.findall(file_name_pattern, issue_text)
        }
        logger.debug(
            f"planning() – Mentioned files extracted from issue: {mentioned_files}"
        )

        # --- 2. Find relevant test files for each mentioned file ---
        # For each mentioned file, look for test files in the repo that match test_* or *_test.*
        text_exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".json"}
        repo_files = set()
        for root, _dirs, files in os.walk(project_path):
            for name in files:
                ext = Path(name).suffix
                if ext in text_exts:
                    abs_path = os.path.abspath(os.path.join(root, name))
                    rel_path = os.path.relpath(abs_path, project_path)
                    repo_files.add(rel_path.replace("\\", "/"))
        logger.debug(
            f"planning() – All repo files with relevant extensions: {repo_files}"
        )

        relevant_test_files = set()
        for mf in mentioned_files:
            base = Path(mf).stem.lower()
            for rf in repo_files:
                rf_name = Path(rf).name.lower()
                # Look for test files that reference the base name
                # Add debug log for each comparison
                logger.debug(
                    f"planning() – Checking if '{rf_name}' is a test file for base '{base}'"
                )
                if ("test" in rf_name or "spec" in rf_name) and (
                    base in rf_name or base in Path(rf).stem.lower()
                ):
                    logger.info(
                        f"planning() – Found relevant test file '{rf}' for base '{base}'"
                    )
                    relevant_test_files.add(rf)

        # --- 3. Combine both sets (dedup) ---
        candidate_files = sorted(mentioned_files | relevant_test_files)
        logger.info(f"planning() – Candidate files for LLM: {candidate_files}")

        # if candidate empty , make candidate all files
        if not candidate_files:
            candidate_files = sorted(repo_files)
            logger.info(f"planning() – No candidate files found, using all repo files: {candidate_files}")

        # --- 4. LLM to classify relevant/source/test files and is_test_generation_issue ---
        prompt = (
            "Given the following GitHub issue and a list of candidate files, "
            "choose ONLY the file(s) that are most relevant for addressing the issue. "
            "Respond in JSON with four keys: "
            "'relevant_files' (all possibly relevant files to the issue query), "
            "'source_files' (the main files which should be updated to fix the issue, should not include test files, ignore README.md), "
            "'test_files' (test files relevant to the issue, including those that may not exist yet), "
            "and 'is_test_generation_issue' (true if the issue is about generating or updating tests, false if it is about code implementation or bugfix).\n\n"
            "Respond ONLY with a valid JSON object, no explanation.\n\n"
            f"Issue:\n{issue_text}\n\n"
            f"Candidate files:\n{', '.join(candidate_files)}"
        )
        logger.debug("planning() – Sending prompt to LLM for file classification.")
        llm_response = self.agent_respond(
            prompt, mode="default", pydantic_object=PlanModel
        )

        try:
            if isinstance(llm_response, dict):
                llm_json = llm_response
            else:
                if not llm_response.strip():
                    llm_json = {
                        "relevant_files": candidate_files,
                        "source_files": candidate_files,
                        "test_files": [],
                        "is_test_generation_issue": False,
                    }
                else:
                    llm_json = json.loads(llm_response)
            relevant_files = llm_json.get("relevant_files", [])
            source_files = llm_json.get("source_files", [])
            test_files = llm_json.get("test_files", [])
            is_test_generation_issue = llm_json.get("is_test_generation_issue", False)
            logger.info(
                f"planning() – LLM classified files: relevant={relevant_files}, source={source_files}, test={test_files}, is_test_generation_issue={is_test_generation_issue}"
            )
        except Exception as e:
            logger.warning(f"Failed to parse LLM JSON output: {e}")
            relevant_files = candidate_files
            source_files = candidate_files
            test_files = []
            is_test_generation_issue = False

        # --- Normalize all test file paths to be under tests/ unless already in a directory ---
        normalized_test_files = []
        for file_path in test_files:
            file_path_obj = Path(file_path)
            if not file_path_obj.parent or str(file_path_obj.parent) == ".":
                test_dir = get_test_dir(project_path)
                norm_path = test_dir.relative_to(project_path) / file_path_obj.name
                normalized_test_files.append(str(norm_path))
                logger.debug(
                    f"planning() – Normalized test file '{file_path}' to '{norm_path}'"
                )
            else:
                normalized_test_files.append(str(file_path_obj))
        test_files = normalized_test_files

        # --- PLAN STRUCTURE ---
        state.plan = {
            "relevant_files": relevant_files,
            "source_files": source_files,
            "test_files": test_files,
            "is_test_generation_issue": is_test_generation_issue,
        }
        logger.info(
            f"planning() – found {len(source_files)} source file(s), {len(test_files)} test file(s), is_test_generation_issue={is_test_generation_issue}"
        )
        return state
