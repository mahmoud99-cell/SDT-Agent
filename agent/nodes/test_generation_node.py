from ..sdt_types import WorkflowState
from ..logging_config import logger
from pathlib import Path
import os
from ..helpers import (
    detect_language_from_context,
    get_test_generation_prompt,
    format_instruction_prompt,
    code_header_remover,
    get_test_dir,
    extract_repo_name_from_url
)


class TestGenerationNode:
    def __init__(self, agent_respond=None):
        self.agent_respond = agent_respond

    def run(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting test_generation phase " + "=" * 20)
        logger.info("test_generation() – Starting test generation for relevant files")
        issue_text = str(state.github_issue) or ""
        language = detect_language_from_context(state)
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            else "https://github.com/SDT-DeveloperTwin/SDT-Testing-Project.git"
        )
        repo_name = extract_repo_name_from_url(repo_url)
        project_path = os.path.join("GitHubIssue", repo_name)

        plan = state.plan or {}
        test_files = plan.get("test_files", [])
        source_files = plan.get("source_files", [])
        relevant_files = plan.get("relevant_files", [])

        # If no test files, infer a test file name and add to test_files
        if not test_files:
            if source_files:
                base_name = Path(source_files[0]).stem
                # Remove leading "test_" if already present to avoid test_test_...
                if base_name.startswith("test_"):
                    base_name = base_name[len("test_") :]
                test_filename = (
                    f"test_{base_name}.py"
                    if language == "python"
                    else f"{base_name}.test.{ 'ts' if language == 'typescript' else 'js'}"
                )
            else:
                test_filename = (
                    "test_generated.py" if language == "python" else "generated.test.ts"
                )
            # --- Use helper to get/create test dir ---
            test_dir = get_test_dir(project_path)
            test_file_path = test_dir / test_filename
            test_files = [str(test_file_path.relative_to(project_path))]
            test_file_path.touch(exist_ok=True)
        # --- Always ensure test files are in tests/ directory unless already specified ---
        normalized_test_files = []
        for file_path in test_files:
            file_path_obj = Path(file_path)
            # If file_path has no parent (i.e., no directory), put it in tests/ or test/
            if not file_path_obj.parent or str(file_path_obj.parent) == ".":
                test_dir = get_test_dir(project_path)
                norm_path = test_dir.relative_to(project_path) / file_path_obj.name
                normalized_test_files.append(str(norm_path))
            else:
                normalized_test_files.append(str(file_path_obj))
        test_files = normalized_test_files

        # Prepare error context if available
        error_context = ""
        if getattr(state, "last_errors", None):
            # Instruct LLM to remove failed tests and keep only the passed ones
            error_context = (
                "\n\n[Previous test/lint errors to fix:]\n"
                + state.last_errors.strip()
                + "\n"
                "Remove any failed test cases and keep only the passed ones. Regenerate only correct and minimal test cases."
            )

        for file_path in test_files:
            file_path_obj = Path(file_path)
            if file_path_obj.is_absolute():
                rel_file_path = file_path_obj.relative_to(project_path)
            else:
                try:
                    rel_file_path = Path(
                        str(file_path).replace(str(project_path) + os.sep, "")
                    )
                except Exception:
                    rel_file_path = file_path_obj
            path_obj = Path(project_path) / rel_file_path
            # Ensure parent directory exists
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            try:
                file_content = path_obj.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Could not read {file_path}: {e}")
                file_content = ""

            # Prepare context for the prompt
            context = (
                f"GitHub Issue:\n{issue_text}\n\n"
                f"Test File to update: {file_path}\nCurrent Test File Content:\n{file_content}\n\n"
                f"Other relevant files for context (do not modify):\n{[f for f in relevant_files if f != file_path]}\n"
                "Only make changes to the test file, use the context for reference."
                f"{error_context}"
            )

            task = get_test_generation_prompt(language)
            prompt = format_instruction_prompt(task, context)
            logger.info(
                f"test_generation() – Calling LLM to generate test code for {file_path}"
            )
            try:
                raw_code = self.agent_respond(prompt)
                new_code = code_header_remover(raw_code)
                if new_code:
                    path_obj.write_text(new_code.rstrip() + "\n", encoding="utf-8")
                    logger.info(
                        f"test_generation() – Generated test code for file: {file_path}"
                    )
                else:
                    logger.warning(
                        f"test_generation() – LLM returned empty content for {file_path}"
                    )
            except Exception as e:
                logger.error(
                    f"test_generation() – Failed to generate test code for {file_path}: {e}"
                )

        # --- FIX: accumulate updated_files ---
        if state.code_changes is None:
            state.code_changes = {"updated_files": []}
        if "updated_files" not in state.code_changes:
            state.code_changes["updated_files"] = []
        for f in test_files:
            if f not in state.code_changes["updated_files"]:
                state.code_changes["updated_files"].append(str(f))
        # ...existing code...
        return state
