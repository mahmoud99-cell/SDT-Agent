from ..sdt_types import WorkflowState
import os
from ..logging_config import logger
from pathlib import Path
from ..helpers import (
    detect_language_from_context,
    get_code_generation_prompt,
    format_instruction_prompt,
    code_header_remover,
    extract_repo_name_from_url
)


class CodeGenerationNode:
    def __init__(self, agent_respond):
        self.agent_respond = agent_respond

    def run(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting code_generation phase " + "=" * 20)
        logger.info("code_generation() – Starting code generation for relevant files")
        # Use github_issue for all prompts (now always the issue body after retrieve_issue_details)
        issue_text = str(state.github_issue) or ""
        if not issue_text:
            logger.error("No issue markdown (did project_understanding() run?)")
            return state

        language = detect_language_from_context(state)
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            else "https://github.com/SDT-DeveloperTwin/SDT-Testing-Project.git"
        )
        repo_name = extract_repo_name_from_url(repo_url)
        project_path = os.path.join("GitHubIssue", repo_name)

        plan = state.plan or {}
        source_files = plan.get("source_files", [])

        relevant_files = plan.get("relevant_files", [])

        # Prepare error context if available
        error_context = ""
        if getattr(state, "last_errors", None):
            error_context = (
                "ATTENTION: The following test or lint errors occurred in the last run. "
                "You MUST fix these errors in your next code update. "
                "Analyze the failed assertion or error message and update the code so that the test passes.\n\n"
                + state.last_errors.strip()
                + "\n"
            )
            logger.info(
                f"code_generation() – Error context for LLM:\n{ state.last_errors.strip()}"
            )
        # ...existing code...

        for file_path in source_files:
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
            try:
                file_content = path_obj.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Could not read {file_path}: {e}")
                file_content = ""

            # Prepare context for the prompt

            # Use full relevant files as context (just file names, not content, to avoid huge prompts)
            context = (
                f"{error_context}"
                f"GitHub Issue:\n{issue_text}\n\n"
                f"Source File to update: {file_path}\nCurrent File Content:\n{file_content}\n\n"
                f"Other relevant files for context (do not modify):\n{[f for f in relevant_files if f != file_path]}\n"
                "Only make changes to the source file, use the context for reference."
            )

            task = get_code_generation_prompt(language)
            prompt = format_instruction_prompt(task, context)
            logger.info(
                f"code_generation() – Calling LLM to generate code for {file_path}"
            )
            try:
                raw_code = self.agent_respond(prompt)
                new_code = code_header_remover(raw_code)
                if new_code:
                    path_obj.write_text(new_code.rstrip() + "\n", encoding="utf-8")
                    logger.info(
                        f"code_generation() – Generated code for file: {file_path}"
                    )
                else:
                    logger.warning(
                        f"code_generation() – LLM returned empty content for {file_path}"
                    )
            except Exception as e:
                logger.error(
                    f"code_generation() – Failed to generate code for {file_path}: {e}"
                )

        # --- FIX: accumulate updated_files ---
        if state.code_changes is None:
            state.code_changes = {"updated_files": []}
        if "updated_files" not in state.code_changes:
            state.code_changes["updated_files"] = []
        # Add new files, avoid duplicates
        for f in source_files:
            if f not in state.code_changes["updated_files"]:
                state.code_changes["updated_files"].append(str(f))
        # ...existing code...
        return state
