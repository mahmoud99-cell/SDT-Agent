import os
import re
import sys
import json
import logging
import subprocess
import strip_markdown
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional, List, TypedDict
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from github import Github
from github import InputGitTreeElement
import argparse  # <-- Add this import

import warnings

warnings.filterwarnings("ignore")

# --- Logging setup ---
logs_dir = os.path.join(os.path.dirname(__file__), "logs")
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M")
log_filename = os.path.join(logs_dir, f"agent_run_output_{timestamp}.txt")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# --- Pydantic Schemas ---
class ProjectContext(BaseModel):
    repo_link: str
    project_description: Optional[str] = None
    language: Optional[str] = None
    relevant_files: Optional[list[str]] = None
    dependencies: Optional[list[str]] = None

    def __str__(self):
        return (
            f"Project Description:\n{self.project_description}\n\n"
            f"Tech Stack:\n{self.language}\n\n"
            f"Relevant Files:\n{self.relevant_files}\n\n"
            f"Dependencies:\n{self.dependencies}"
        )


class TestResultsModel(TypedDict, total=False):
    test_outputs: dict[str, str]
    tests_passed: bool


class LintResultsModel(TypedDict, total=False):
    lint_outputs: dict[str, str]
    lint_passed: bool


class PlanModel(BaseModel):
    relevant_files: List[str] = Field(
        default_factory=list,
        description="List of all possibly relevant files to the GitHub issue.",
    )
    source_files: List[str] = Field(
        default_factory=list,
        description="List of the main files which should be updated to fix the issue.",
    )
    test_files: List[str] = Field(
        default_factory=list,
        description="List of test files relevant to the GitHub issue.",
    )
    is_test_generation_issue: bool = Field(
        default=False,
        description="True if the issue is about generating or updating tests, False if it is about code implementation or bugfix.",
    )


class WorkflowState(BaseModel):
    github_issue: str
    project_context: Optional[ProjectContext] = None
    plan: Optional[dict] = None
    code_changes: Optional[dict] = None
    test_results: Optional[TestResultsModel] = None
    npm_command: Optional[str] = None
    test_cases: Optional[str] = None
    npm_output: Optional[str] = None
    lint_results: Optional[LintResultsModel] = None
    passed: Optional[bool] = None


class SDTAgent:
    def __init__(self):
        # --- Environment setup ---
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        os.chdir(project_root)
        load_dotenv()

        self.huggingface_api_key = os.getenv("HF_TOKEN")
        self.github_app_id = os.getenv("GITHUB_APP_ID")
        self.github_app_private_key_path = "./sdt-sw-buddy.2025-07-11.private-key.pem"
        if os.path.exists(self.github_app_private_key_path):
            with open(self.github_app_private_key_path, "r", encoding="utf-8") as f:
                self.github_app_private_key = f.read()
        else:
            self.github_app_private_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
        self.github_repository = os.getenv("GITHUB_REPOSITORY")
        self.github_branch = os.getenv("GITHUB_BRANCH")
        self.github_base_branch = os.getenv("GITHUB_BASE_BRANCH")
        self.repo_name = os.getenv("GITHUB_REPOSITORY")
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        os.environ["GOOGLE_API_KEY"] = self.google_api_key

        self.model = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            google_api_key=self.google_api_key,
            temperature=0.2,
        )
        self.retry_count = 0
        self.max_retries = 3
        self.last_errors = None  # <-- Track last errors for retries
        self.config = {"configurable": {"thread_id": "1"}}

    # --- Helper functions ---
    def agent_respond(
        self, prompt: str, mode: str = "default", pydantic_object: BaseModel = None
    ):
        """
        mode: "default" (plain code), "patch" (unified diff)
        """
        if mode == "patch":
            patch_prompt = (
                "You are an expert software engineer. "
                "Given the following problem statement, generate a unified diff (patch) "
                "in the standard format (starting with 'diff --git ...') that applies the fix. "
                "Do NOT include explanations, only the diff. "
                "If the fix cannot be expressed as a diff, return an empty string.\n\n"
                f"{prompt}"
            )
            logger.debug(
                "agent_respond() – Patch prompt length: %s tokens (approx.)",
                len(patch_prompt.split()),
            )
            response = self.model.invoke(patch_prompt)
            content = (
                response.content if hasattr(response, "content") else str(response)
            )
            # Only return if it looks like a diff
            if content.lstrip().startswith("diff "):
                return content
            logger.warning("LLM did not return a unified diff for patch task.")
            return ""
        else:
            logger.debug(
                "agent_respond() – LLM prompt length: %s tokens (approx.)",
                len(prompt.split()),
            )
            if pydantic_object:
                logger.info(
                    "agent_respond() – Using structured output with Pydantic object."
                )
                structured_llm = self.model.with_structured_output(pydantic_object)
                response = structured_llm.invoke(prompt)
                if isinstance(response, BaseModel):
                    logger.info(
                        "agent_respond() – LLM response is a Pydantic object. Parsing it to dict."
                    )
                    response = response.model_dump()
                logger.info(
                    f"agent_respond() – LLM response parsed into Pydantic object: {response}"
                )
                return response
            else:
                response = self.model.invoke(prompt)
                return (
                    response.content if hasattr(response, "content") else str(response)
                )

    def format_instruction_prompt(self, task: str, context: str) -> str:
        return (
            "You are a senior software engineer.\n"
            "Respond ONLY with valid, runnable code for the project's tech stack. "
            "Do NOT wrap the code in markdown fences. "
            "Do NOT include test functions, test cases, or usage examples unless explicitly requested. "
            "Ensure the code is idiomatic and follows best practices for the relevant language and tools.\n\n"
            f"Task:\n{task}\n\nContext:\n{context}"
        )

    def get_code_generation_prompt(self, language: str) -> str:
        lang_str = f"{language.capitalize()} " if language else ""
        return (
            f"Implement the following GitHub issue in the {lang_str}source file provided.\n"
            "- Output the full, valid code for the file (not just a section).\n"
            "- Do NOT include markdown code fences in the response.\n"
            "- Do NOT create or modify test functions, test cases, or usage examples unless explicitly requested.\n"
            "- Maintain correctness and code quality according to the project's tech stack and tools.\n"
        )

    def get_test_generation_prompt(self, language: str) -> str:
        lang_str = f"{language.capitalize()} " if language else ""
        return (
            f"Implement the following GitHub issue in the {lang_str}test file provided.\n"
            "- Output the full, valid code for the test file (not just a section).\n"
            "- Do NOT include markdown code fences in the response.\n"
            "- Ensure the tests are idiomatic and follow best practices for the project's tech stack.\n"
            "- Use only valid, working import statements that match the actual file/module structure of the project. "
            "Determine the correct import path based on the real file locations and names provided in the context. "
            "Do NOT use placeholder or non-existent module names such as 'your_project' or 'src' unless they are present in the project structure. "
            "If the source file is at the project root, import directly by filename (e.g., 'from validation import ...').\n"
            "- Generate VERY FEW and SIMPLE, to-the-point test cases that directly check the main requirements. "
            "Avoid redundant or excessive tests; focus only on the core functionality described in the issue.\n"
        )

    def code_header_remover(self, raw_code: str) -> str:
        """
        Removes markdown code fences and leading/trailing whitespace from code.
        """
        code = raw_code.strip()
        code = re.sub(r"^```[a-zA-Z]*\s*|```$", "", code, flags=re.MULTILINE).strip()
        return code

    @staticmethod
    def is_test_file(f):
        name = Path(f).name.lower()
        return "test" in name or "spec" in name

    def code_generation(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting code_generation phase " + "=" * 20)
        logger.info("code_generation() – Starting code generation for relevant files")
        # Use github_issue for all prompts (now always the issue body after retrieve_issue_details)
        issue_text = str(state.github_issue) or ""
        if not issue_text:
            logger.error("No issue markdown (did project_understanding() run?)")
            return state

        language = self.detect_language_from_context(state)
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            else "https://github.com/SDT-DeveloperTwin/SDT-Testing-Project.git"
        )
        repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
        project_path = os.path.join("GitHubIssue", repo_name)

        plan = state.plan or {}
        source_files = plan.get("source_files", [])

        relevant_files = plan.get("relevant_files", [])

        # Prepare error context if available
        error_context = ""
        if getattr(self, "last_errors", None):
            error_context = (
                "ATTENTION: The following test or lint errors occurred in the last run. "
                "You MUST fix these errors in your next code update. "
                "Analyze the failed assertion or error message and update the code so that the test passes.\n\n"
                + self.last_errors.strip()
                + "\n"
            )
            logger.info(
                f"code_generation() – Error context for LLM:\n{self.last_errors.strip()}"
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

            task = self.get_code_generation_prompt(language)
            prompt = self.format_instruction_prompt(task, context)
            logger.info(
                f"code_generation() – Calling LLM to generate code for {file_path}"
            )
            try:
                raw_code = self.agent_respond(prompt)
                new_code = self.code_header_remover(raw_code)
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

    def test_generation(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting test_generation phase " + "=" * 20)
        logger.info("test_generation() – Starting test generation for relevant files")
        issue_text = str(state.github_issue) or ""
        language = self.detect_language_from_context(state)
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            else "https://github.com/SDT-DeveloperTwin/SDT-Testing-Project.git"
        )
        repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
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
            test_dir = self._get_test_dir(project_path)
            test_file_path = test_dir / test_filename
            test_files = [str(test_file_path.relative_to(project_path))]
            test_file_path.touch(exist_ok=True)
        # --- Always ensure test files are in tests/ directory unless already specified ---
        normalized_test_files = []
        for file_path in test_files:
            file_path_obj = Path(file_path)
            # If file_path has no parent (i.e., no directory), put it in tests/ or test/
            if not file_path_obj.parent or str(file_path_obj.parent) == ".":
                test_dir = self._get_test_dir(project_path)
                norm_path = test_dir.relative_to(project_path) / file_path_obj.name
                normalized_test_files.append(str(norm_path))
            else:
                normalized_test_files.append(str(file_path_obj))
        test_files = normalized_test_files

        # Prepare error context if available
        error_context = ""
        if getattr(self, "last_errors", None):
            # Instruct LLM to remove failed tests and keep only the passed ones
            error_context = (
                "\n\n[Previous test/lint errors to fix:]\n"
                + self.last_errors.strip()
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

            task = self.get_test_generation_prompt(language)
            prompt = self.format_instruction_prompt(task, context)
            logger.info(
                f"test_generation() – Calling LLM to generate test code for {file_path}"
            )
            try:
                raw_code = self.agent_respond(prompt)
                new_code = self.code_header_remover(raw_code)
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

    def _get_test_dir(self, project_path):
        """
        Returns the path to the test directory, preferring 'tests' if it exists,
        otherwise 'test', otherwise creates 'tests'.
        """
        tests_dir = Path(project_path, "tests")
        test_dir = Path(project_path, "test")
        if tests_dir.exists() and tests_dir.is_dir():
            return tests_dir
        elif test_dir.exists() and test_dir.is_dir():
            return test_dir
        else:
            tests_dir.mkdir(parents=True, exist_ok=True)
            return tests_dir

    def detect_language_from_context(self, state: WorkflowState) -> str:
        if state.project_context and hasattr(state.project_context, "language"):
            tech = state.project_context.language.lower()
            if "python" in tech:
                return "python"
            if "typescript" in tech or "ts" in tech:
                return "typescript"
            if "javascript" in tech or "js" in tech:
                return "javascript"
        if state.plan and "relevant_files" in state.plan:
            for f in state.plan["relevant_files"]:
                ext = Path(f).suffix.lower()
                if ext == ".py":
                    return "python"
                if ext in {".ts", ".tsx"}:
                    return "typescript"
                if ext in {".js", ".jsx"}:
                    return "javascript"
        return "python"

    # --- Pipeline Phases ---
    def clone_project_repo(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting initialize_issue_repo phase " + "=" * 20)
        logger.info("initialize_issue_repo() – Cloning GitHub repository...")
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            and getattr(state.project_context, "repo_link", None)
            else "https://github.com/mahmoud99-cell/SDT-Testing-Project.git"
        )
        repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
        destination_dir = os.path.join("GitHubIssue", repo_name)
        if not os.path.exists(destination_dir):
            os.makedirs("GitHubIssue", exist_ok=True)
            logger.info(
                f"initialize_issue_repo() – Cloning repository {repo_url} into {destination_dir}..."
            )
            subprocess.run(
                ["git", "clone", repo_url, destination_dir],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            logger.info("initialize_issue_repo() – Repository cloned successfully.")
        else:
            logger.info(
                f"initialize_issue_repo() – Repository already exists at {destination_dir}. Skipping clone."
            )
        return state

    def retrieve_issue_details(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting retrieve_issue_details phase " + "=" * 20)
        logger.info(
            "retrieve_issue_details() – Loading issue from state.github_issue..."
        )
        # Check if github_issue is a number (as string), fetch from GitHub if so
        if state.github_issue and self._is_issue_number(state.github_issue):
            issue_number = int(state.github_issue)
            github_token = os.getenv("GITHUB_TOKEN")
            repo_name = os.getenv("GITHUB_REPOSITORY")
            if not github_token or not repo_name:
                raise ValueError(
                    "Missing GitHub credentials.: GITHUB_TOKEN or GITHUB_REPOSITORY not set in environment."
                )
            g = Github(github_token)
            repo = g.get_repo(repo_name)
            open_issue = repo.get_issue(number=issue_number)
            try:
                if not open_issue or not open_issue.body:
                    raise ValueError(f"Issue #{issue_number} has no body.")
                if open_issue.state.lower() != "open":
                    raise ValueError(f"Issue #{issue_number} is not open.")
                issue = open_issue.body
                # Overwrite github_issue with the issue body (as string)
                state.github_issue = strip_markdown.strip_markdown(issue)
                logger.info(
                    f"retrieve_issue_details() – Fetched issue #{issue_number} from GitHub. Preview:\n"
                    + (issue[:300] if issue else "")
                )
            except Exception as e:
                logger.error(f"Could not fetch issue #{issue_number}: {e}")
                raise
        else:
            if not state.github_issue:
                raise ValueError("github_issue is empty in WorkflowState.")
            logger.info(
                "retrieve_issue_details() – Issue loaded. Preview:\n"
                + str(state.github_issue)[:300]
            )
            # github_issue is already set as issue body
        return state

    @staticmethod
    def _is_issue_number(issue_str: str) -> bool:
        """
        Returns True if the string represents an integer (issue number), False otherwise.
        """
        try:
            int(issue_str)
            return True
        except Exception:
            return False

    def planning(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting planning phase " + "=" * 20)
        # Extract the issue text from the state
        issue_text = str(state.github_issue) or ""
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            else "https://github.com/SDT-DeveloperTwin/SDT-Testing-Project.git"
        )
        repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
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
        mentioned_files = set(
            m.replace("\\", "/") for m in re.findall(file_name_pattern, issue_text)
        )
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

        # --- 4. LLM to classify relevant/source/test files and is_test_generation_issue ---
        prompt = (
            "Given the following GitHub issue and a list of candidate files, "
            "choose ONLY the file(s) that are most relevant for addressing the issue. "
            "Respond in JSON with four keys: "
            "'relevant_files' (all possibly relevant files to the issue query), "
            "'source_files' (the main files which should be updated to fix the issue, should not include test files), "
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
                test_dir = self._get_test_dir(project_path)
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

    def is_test_generation_issue(self, state: WorkflowState) -> bool:
        """
        Heuristic to determine if the issue is about test generation.
        """
        issue_text = str(state.github_issue).lower()
        # Simple heuristics: look for keywords
        test_keywords = [
            "test case",
            "test file",
            "add test",
            "write test",
            "unit test",
            "unit tests",
            "integration test",
            "pytest",
            "jest",
            "mocha",
            "add coverage",
            "missing test",
            "missing tests",
        ]
        return any(kw in issue_text for kw in test_keywords)

    def main_code_generation(self, state: WorkflowState) -> WorkflowState:
        """
        Runs code_generation for source_files and test_generation for test_files.
        Handles cases where only one or both are present.
        Uses plan['is_test_generation_issue'] to decide which to run.
        """
        plan = state.plan or {}
        source_files = plan.get("source_files", [])
        test_files = plan.get("test_files", [])
        is_test_generation_issue = plan.get("is_test_generation_issue", False)

        if is_test_generation_issue:
            if test_files:
                logger.info(
                    "main_code_generation: Detected test generation issue. Running test_generation only."
                )
                state = self.test_generation(state)
        else:
            if source_files:
                logger.info(
                    "main_code_generation: Detected code issue. Running code_generation for source files."
                )
                state = self.code_generation(state)
            # if test_files:
            #     logger.info("main_code_generation: Also running test_generation for test files.")
            #     state = self.test_generation(state)
        return state

    def build_phases(self, include_commit_and_pr: bool = True):
        phases = [
            ("issue_analysis", self.clone_project_repo),
            ("project_understanding", self.retrieve_issue_details),
            ("planning", self.planning),
            (
                "main_code_generation",
                self.main_code_generation,
            ),  # <--- use main node here
            ("test_and_lint", self.test_and_lint),
        ]
        if include_commit_and_pr:
            phases.append(("commit_and_pr", self.commit_and_pr))
        return phases

    def test_and_lint(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting test_and_lint phase " + "=" * 20)
        logger.info("test_and_lint() – Running test_and_lint phase...")
        # Use state.plan for relevant_files, test_files, etc.
        plan = state.plan or {}
        relevant_files = plan.get("relevant_files", [])
        test_files = plan.get("test_files", [])

        if not relevant_files:
            logger.warning("No relevant files found for testing/linting.")
            state.test_results = {}
            state.lint_results = {}
            state.passed = None
            return state

        # Always use the correct repo path for test/lint execution
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            and getattr(state.project_context, "repo_link", None)
            else "https://github.com/SDT-DeveloperTwin/SDT-Testing-Project.git"
        )
        repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
        project_path = os.path.join("GitHubIssue", repo_name)

        if not os.path.isdir(project_path):
            raise FileNotFoundError(
                f"Repository path '{project_path}' not found; did issue_analysis run?"
            )
        if state.project_context and hasattr(state.project_context, "dependencies"):
            dependencies = state.project_context.dependencies
            if dependencies is not None:
                if isinstance(dependencies, str):
                    dependencies_list = [
                        dep.strip() for dep in dependencies.split(",") if dep.strip()
                    ]
                else:
                    dependencies_list = list(dependencies)
                self.ensure_test_lint_dependencies(
                    dependencies_list, state.project_context.language
                )

        # Use test_files from plan, fallback to files in relevant_files that look like tests
        test_files_exist = [
            f for f in test_files if Path(os.path.join(project_path, f)).is_file()
        ]
        if not test_files_exist:
            logger.info(
                "No test files found in plan, using relevant files for testing."
            )
            test_files = [f for f in relevant_files if self.is_test_file(f)]
            test_files_exist = [
                f for f in test_files if Path(os.path.join(project_path, f)).is_file()
            ]

        # --- If still no test files, generate tests and update test_files ---
        if not test_files_exist:
            logger.info("No test files found after fallback. Generating tests.")
            state = self.test_generation(state)
            plan = state.plan or {}
            test_files = plan.get("test_files", [])
            test_files_exist = [
                f for f in test_files if Path(os.path.join(project_path, f)).is_file()
            ]
            # If still empty, try to infer from updated_files in code_changes
            if (
                not test_files_exist
                and state.code_changes
                and "updated_files" in state.code_changes
            ):
                language = self.detect_language_from_context(state)
                base_name = (
                    Path(state.code_changes["updated_files"][0]).stem
                    if state.code_changes["updated_files"]
                    else "generated"
                )
                # Remove leading "test_" if already present to avoid test_test_...
                if base_name.startswith("test_"):
                    base_name = base_name[len("test_") :]
                test_filename = (
                    f"test_{base_name}.py"
                    if language == "python"
                    else f"{base_name}.test.{ 'ts' if language == 'typescript' else 'js'}"
                )
                test_dir = self._get_test_dir(project_path)
                test_file_path = test_dir / test_filename
                test_files = [str(test_file_path.relative_to(project_path))]
                test_file_path.touch(exist_ok=True)
                test_files_exist = [str(test_file_path.relative_to(project_path))]

        logger.info(f"test_and_lint() – Test files to run: {test_files_exist}")
        test_outputs = {}
        lint_outputs = {}
        all_tests_passed = True
        all_lint_passed = True
        error_accum = []  # <-- Collect errors for retry context
        for file_path in test_files_exist:
            ext = Path(file_path).suffix.lower()
            test_cmd = None
            if ext == ".py":
                test_cmd = f"pytest {file_path}"
            elif ext in {".js", ".jsx", ".ts", ".tsx"}:
                test_cmd = "npm run test"
            elif ext == ".json":
                continue
            else:
                continue
            try:
                logger.info(f"test_and_lint() – Running test command: {test_cmd}")
                test_abs_path = os.path.abspath(os.path.join(project_path, file_path))
                logger.info(
                    f"test_and_lint() – Absolute path for test file: {test_abs_path}"
                )
                repo_root = os.path.abspath(project_path)
                logger.info(
                    f"test_and_lint() – Final repo_root used as cwd: {repo_root}"
                )
                if ext == ".py":
                    test_rel_path = os.path.relpath(test_abs_path, repo_root)
                    test_cmd = f"pytest {test_rel_path}"
                    env = os.environ.copy()
                    env["PYTHONPATH"] = (
                        repo_root + os.pathsep + env.get("PYTHONPATH", "")
                    )
                else:
                    env = None

                # --- Run test subprocess, parse for missing modules ---
                test_proc = subprocess.run(
                    test_cmd,
                    shell=True,
                    text=True,
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                test_output = test_proc.stdout
                test_error = test_proc.stderr

                # Check for ModuleNotFoundError in stderr and try to install missing module
                if (
                    ext == ".py"
                    and "ModuleNotFoundError" in test_error
                    and "No module named" in test_error
                ):
                    import re

                    missing_mods = re.findall(
                        r"ModuleNotFoundError: No module named '([^']+)'", test_error
                    )
                    for missing_module in set(missing_mods):
                        logger.info(
                            f"Module '{missing_module}' not found. Attempting to install..."
                        )
                        try:
                            subprocess.check_call(
                                [sys.executable, "-m", "pip", "install", missing_module]
                            )
                            logger.info(
                                f"Installed '{missing_module}'. Retrying test..."
                            )
                        except Exception as e:
                            logger.error(f"Failed to install '{missing_module}': {e}")
                    # Retry test after installing missing modules
                    test_proc = subprocess.run(
                        test_cmd,
                        shell=True,
                        text=True,
                        cwd=repo_root,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env,
                    )
                    test_output = test_proc.stdout
                    test_error = test_proc.stderr

                # --- Handle ImportError for unbuilt C extensions (e.g., astropy) ---
                if (
                    ext == ".py"
                    and "ImportError" in test_error
                    and (
                        "pip install -e ." in test_error
                        or "python setup.py build_ext --inplace" in test_error
                    )
                ):
                    logger.info(
                        "Detected ImportError due to missing C extensions. Attempting to build extensions with 'pip install -e .'"
                    )
                    try:
                        subprocess.check_call(
                            [sys.executable, "-m", "pip", "install", "-e", "."],
                            cwd=repo_root,
                        )
                        logger.info(
                            "Successfully ran 'pip install -e .'. Retrying test..."
                        )
                        test_proc = subprocess.run(
                            test_cmd,
                            shell=True,
                            text=True,
                            cwd=repo_root,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            env=env,
                        )
                        test_output = test_proc.stdout
                        test_error = test_proc.stderr
                    except Exception as e:
                        logger.error(f"Failed to build C extensions: {e}")

                test_outputs[file_path] = (
                    f"[Exit code: {test_proc.returncode}]\n"
                    f"STDOUT:\n{test_output}\nSTDERR:\n{test_error}"
                )
                if test_proc.returncode != 0:
                    all_tests_passed = False
                    logger.error(
                        f"test_and_lint() – Test failed for {file_path}:\nSTDOUT:\n{test_output}\nSTDERR:\n{test_error}"
                    )
                    error_accum.append(
                        f"Test failure in {file_path}:\n{test_output}\n{test_error}"
                    )
            except Exception as e:
                test_outputs[file_path] = f"Test error: {e}"
                logger.error(
                    f"test_and_lint() – Error while running tests on {file_path}: {e}"
                )
                all_tests_passed = False
                error_accum.append(f"Test error in {file_path}: {e}")
        for file_path in relevant_files:
            if file_path.endswith(".py") and "test" not in Path(file_path).stem.lower():
                lint_cmd = f"ruff check {file_path}"
                try:
                    # Use the same logic as for tests: run from repo_root and use relative path
                    lint_abs_path = os.path.abspath(
                        os.path.join(project_path, file_path)
                    )
                    repo_root = os.path.abspath(project_path)
                    lint_rel_path = os.path.relpath(lint_abs_path, repo_root)
                    lint_cmd = f"ruff check {lint_rel_path}"
                    logger.info(f"test_and_lint() – Linting file: {file_path}")
                    logger.info(
                        f"test_and_lint() – Absolute path for lint file: {lint_abs_path}"
                    )
                    logger.info(
                        f"test_and_lint() – Running lint command: {lint_cmd} (cwd={repo_root})"
                    )
                    lint_proc = subprocess.run(
                        lint_cmd,
                        shell=True,
                        text=True,
                        cwd=repo_root,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    lint_outputs[file_path] = (
                        f"[Exit code: {lint_proc.returncode}]\nSTDOUT:\n{lint_proc.stdout}\nSTDERR:\n{lint_proc.stderr}"
                    )
                    if lint_proc.returncode != 0:
                        all_lint_passed = False
                        error_accum.append(
                            f"Lint failure in {file_path}: Exit code {lint_proc.returncode}"
                        )
                except Exception as e:
                    lint_outputs[file_path] = f"Lint error: {e}"
                    logger.error(
                        f"test_and_lint() – Error while linting {file_path}: {e}"
                    )
                    all_lint_passed = False
                    error_accum.append(f"Lint error in {file_path}: {e}")
        state.test_results = {
            "test_outputs": test_outputs,
            "tests_passed": all_tests_passed,
        }
        state.lint_results = {
            "lint_outputs": lint_outputs,
            "lint_passed": all_lint_passed,
        }
        state.passed = all_tests_passed and all_lint_passed
        logger.info("test_and_lint() – Test and lint results:")
        logger.info(f"test_and_lint() – Tests passed: {all_tests_passed}")
        logger.info(f"test_and_lint() – Lint passed: {all_lint_passed}")
        # Save errors for next retry
        self.last_errors = "\n\n".join(error_accum) if error_accum else None
        return state

    def should_retry(self, state: WorkflowState, include_commit_and_pr: bool) -> str:
        # Use self.retry_count and self.max_retries for retry logic
        if state.test_results and not state.test_results.get("tests_passed"):
            if not hasattr(self, "retry_count"):
                self.retry_count = 0
            if not hasattr(self, "max_retries"):
                self.max_retries = 3
            retry_attempt = self.retry_count
            if retry_attempt < self.max_retries:
                logger.info(
                    f"Tests did not pass. Retrying code_generation (attempt {retry_attempt + 1}/{self.max_retries})."
                )
                self.retry_count += 1
                # self.last_errors is already set by test_and_lint
                return "main_code_generation"
            else:
                logger.info(
                    "Tests did not pass and retry limit reached. Not retrying. :("
                )
                self.last_errors = None  # Clear errors after giving up
                return END
        else:
            logger.info("Tests passed or not applicable. Proceeding to commit_and_pr.")
            self.last_errors = None  # Clear errors on success
        if include_commit_and_pr:
            return (
                "commit_and_pr"
                if state.test_results and state.test_results.get("tests_passed")
                else END
            )
        else:
            return END

    def commit_and_pr(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting commit_and_pr phase " + "=" * 20)
        logger.info("commit_and_pr() – Starting commit_and_pr phase...")
        if state.test_results and state.test_results.get("tests_passed"):
            logger.info(
                "commit_and_pr() – All tests and lint checks passed. Preparing to commit and push directly to main."
            )
            # Always generate commit message here
            commit_message = state.code_changes.get("commit_message", "")
            if not commit_message:
                # Prefer to use code diff or file content for commit message context
                changed_files = []
                if "created_file" in state.code_changes:
                    changed_files.append(state.code_changes["created_file"])
                if "updated_files" in state.code_changes:
                    changed_files.extend(state.code_changes["updated_files"])
                changed_files = list(set(changed_files))
                file_summaries = []
                repo_name = os.getenv("GITHUB_REPOSITORY") or "SDT-Testing-Project"
                project_path = os.path.join("GitHubIssue", repo_name)
                for file_path in changed_files:
                    local_path = os.path.join(project_path, file_path)
                    try:
                        with open(local_path, "r", encoding="utf-8") as f:
                            file_content = f.read(500)
                        file_summaries.append(f"{file_path}:\n{file_content}")
                    except Exception:
                        continue
                summary = "\n\n".join(file_summaries)
                prompt = (
                    f"Generate a short, conventional Git commit message for the following changes.\n"
                    f"GitHub Issue:\n{str(state.github_issue)}\n\n"
                    f"Changed files and content (truncated):\n{summary}"
                )
                commit_message = self.agent_respond(prompt)
                state.code_changes["commit_message"] = commit_message
            logger.info(
                f"commit_and_pr() – Commit message generated: {state.code_changes['commit_message']}"
            )

            github_token = os.getenv("GITHUB_TOKEN")
            repo_name = os.getenv("GITHUB_REPOSITORY")
            base_branch = os.getenv("GITHUB_BASE_BRANCH", "main")
            logger.info(
                f"commit_and_pr() – Connecting to GitHub repo: {repo_name} on branch: {base_branch}"
            )

            g = Github(github_token)
            repo = g.get_repo(repo_name)

            source_branch = repo.get_branch(base_branch)
            new_ref = repo.get_git_ref(f"heads/{base_branch}")

            changed_files = []
            if "created_file" in state.code_changes:
                changed_files.append(state.code_changes["created_file"])
            if "updated_files" in state.code_changes:
                changed_files.extend(state.code_changes["updated_files"])
            changed_files = list(set(changed_files))

            commit_files: list[InputGitTreeElement] = []
            for file_path in changed_files:
                local_path = os.path.join(
                    "GitHubIssue", "SDT-Testing-Project", file_path
                )
                try:
                    with open(local_path, "r", encoding="utf-8") as f:
                        data = f.read()
                    rel_path = file_path.replace("\\", "/").lstrip("/")
                    commit_files.append(
                        InputGitTreeElement(
                            path=rel_path,
                            mode="100644",
                            type="blob",
                            content=data,
                        )
                    )
                except Exception as e:
                    logger.error(f"Failed to read file for commit: {file_path}: {e}")

            if commit_files:
                tree = repo.create_git_tree(
                    commit_files, base_tree=source_branch.commit.commit.tree
                )
                parent_commit = repo.get_git_commit(source_branch.commit.sha)
                commit = repo.create_git_commit(
                    message=commit_message,
                    tree=tree,
                    parents=[parent_commit],
                )
                logger.info(f"commit_and_pr() – Commit created: {commit.sha}")

                logger.info(
                    f"commit_and_pr() – Updating branch {base_branch} with new commit."
                )
                new_ref.edit(commit.sha)
            else:
                logger.warning("commit_and_pr() – No files to commit. Skipping commit.")
        else:
            logger.warning(
                "commit_and_pr() – Tests or lint did not pass. Skipping commit."
            )
        return state

    def ensure_test_lint_dependencies(self, dependencies: list[str], project_type: str):
        logger.info(
            "=" * 20 + " starting ensure_test_lint_dependencies phase " + "=" * 20
        )
        logger.info(
            f"ensure_test_lint_dependencies() – Ensuring dependencies for project type: {project_type}"
        )
        if not dependencies:
            logger.info("ensure_test_lint_dependencies() – No dependencies to install.")
            dependencies = []

        # --- Workspace/venv handling for Python projects ---
        if project_type and project_type.lower() == "python":
            # Determine project root (assume cwd is agent/, go up to GitHubIssue/...)
            project_root = None
            for d in os.listdir("GitHubIssue"):
                candidate = os.path.join("GitHubIssue", d)
                if os.path.isdir(candidate):
                    project_root = candidate
                    break
            if not project_root:
                logger.warning(
                    "ensure_test_lint_dependencies() – Could not find project root for venv setup."
                )
                project_root = os.getcwd()

            # Extract repo_name from project_root path
            repo_name = os.path.basename(os.path.normpath(project_root))

            venv_dir = os.path.join(project_root, f".venv_{repo_name}")
            python_exe = (
                os.path.join(venv_dir, "Scripts", "python.exe")
                if os.name == "nt"
                else os.path.join(venv_dir, "bin", "python")
            )

            # Create venv if not exists
            if not os.path.exists(venv_dir):
                logger.info(
                    f"ensure_test_lint_dependencies() – Creating venv at {venv_dir}"
                )
                subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
            else:
                logger.info(
                    f"ensure_test_lint_dependencies() – venv already exists at {venv_dir}"
                )

            # Install from requirements.txt if exists
            req_file = os.path.join(project_root, "requirements.txt")
            pyproject_file = os.path.join(project_root, "pyproject.toml")
            setup_file = os.path.join(project_root, "setup.py")
            if os.path.isfile(req_file):
                logger.info(
                    f"ensure_test_lint_dependencies() – Installing from requirements.txt"
                )
                subprocess.run(
                    [python_exe, "-m", "pip", "install", "-r", req_file], check=True
                )
            elif os.path.isfile(pyproject_file) and os.path.isfile(setup_file):
                logger.info(
                    "ensure_test_lint_dependencies() – No requirements.txt found, but pyproject.toml and setup.py exist. Installing with 'pip install .'"
                )
                try:
                    subprocess.run(
                        [python_exe, "-m", "pip", "install", "."],
                        cwd=project_root,
                        check=True,
                    )
                except subprocess.CalledProcessError as e:
                    logger.error(
                        f"ensure_test_lint_dependencies() – 'pip install .' failed: {e}. Continuing with rest of setup."
                    )
            # Always install pytest and ruff
            logger.info(
                "ensure_test_lint_dependencies() – Installing pytest and ruff in venv"
            )
            subprocess.run(
                [python_exe, "-m", "pip", "install", "--upgrade", "pip"], check=True
            )
            subprocess.run(
                [python_exe, "-m", "pip", "install", "pytest", "ruff"], check=True
            )
            # Install any additional dependencies
            for dep in dependencies:
                try:
                    logger.info(
                        f"ensure_test_lint_dependencies() – Installing Python dependency in venv: {dep}"
                    )
                    subprocess.run(
                        [python_exe, "-m", "pip", "install", dep], check=True
                    )
                    logger.info(
                        f"ensure_test_lint_dependencies() – Successfully installed '{dep}'."
                    )
                except subprocess.CalledProcessError as e:
                    logger.error(
                        f"ensure_test_lint_dependencies() – Failed to install '{dep}': {e}"
                    )
        elif project_type.lower() in {"react", "javascript", "typescript", "js", "ts"}:
            for dep in dependencies:
                try:
                    logger.info(
                        f"ensure_test_lint_dependencies() – Installing npm dependency: {dep}"
                    )
                    subprocess.run(["npm", "install", dep], check=True)
                    logger.info(
                        f"ensure_test_lint_dependencies() – Successfully installed '{dep}'."
                    )
                except subprocess.CalledProcessError as e:
                    logger.error(
                        f"ensure_test_lint_dependencies() – Failed to install '{dep}': {e}"
                    )
        else:
            logger.warning(
                f"ensure_test_lint_dependencies() – Dependency installation not supported for project type: {project_type}"
            )

    def run_workflow(
        self, initial_state: WorkflowState, include_commit_and_pr: bool = True
    ):
        logger.info("=" * 20 + " starting run_workflow phase " + "=" * 20)
        logger.info("run_workflow() – Starting workflow execution")
        graph = StateGraph(WorkflowState)
        phases = self.build_phases(include_commit_and_pr=include_commit_and_pr)
        for name, func in phases:
            graph.add_node(name, func)
        graph.set_entry_point(phases[0][0])
        # Only add unconditional edges up to test_and_lint
        for i in range(len(phases) - 1):
            # Only add edge if not from test_and_lint to commit_and_pr
            if not (
                phases[i][0] == "test_and_lint" and phases[i + 1][0] == "commit_and_pr"
            ):
                graph.add_edge(phases[i][0], phases[i + 1][0])
        # Conditional edge from test_and_lint
        if include_commit_and_pr:
            graph.add_conditional_edges(
                "test_and_lint",
                lambda state: self.should_retry(state, include_commit_and_pr),
                {
                    "main_code_generation": "main_code_generation",
                    "commit_and_pr": "commit_and_pr",
                    END: END,
                },
            )
            graph.set_finish_point("commit_and_pr")
        else:
            graph.add_conditional_edges(
                "test_and_lint",
                lambda state: self.should_retry(state, include_commit_and_pr),
                {
                    "main_code_generation": "main_code_generation",
                    END: END,
                },
            )
            graph.set_finish_point("test_and_lint")

        workflow = graph.compile()

        try:
            final_state = workflow.invoke(initial_state)
            logger.info("Workflow completed successfully!")
            # Prettify final_state output
            try:
                pretty_state = json.dumps(
                    (
                        final_state
                        if isinstance(final_state, dict)
                        else final_state.model_dump()
                    ),
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
                logger.info(f"Final General Coding Project State:\n{pretty_state}")
            except Exception as e:
                logger.info(f"Final General Coding Project State: {final_state}")
            return final_state
        except Exception as e:
            logger.error(f"Workflow failed: {str(e)}")
            traceback.print_exc()
            return None


# --- Example pipeline run ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run SDT Buddy Agent for a GitHub issue."
    )
    parser.add_argument(
        "--github_issue",
        type=str,
        required=True,
        help="GitHub issue number (as string/int), path to a markdown file containing the issue, or the issue content itself.",
    )
    parser.add_argument(
        "--repo_link",
        type=str,
        required=False,
        default="https://github.com/SDT-DeveloperTwin/SDT-Testing-Project.git",
        help="Optional: GitHub repository link to use.",
    )

    parser.add_argument(
        "--include_commit_and_pr",
        type=str,
        default="true",
        help="Include commit and PR phase in the workflow. Use 'true' (default) or 'false'.",
    )
    args = parser.parse_args()

    github_issue_arg = args.github_issue
    repo_link = args.repo_link
    include_commit_and_pr = str(args.include_commit_and_pr).lower() in (
        "1",
        "true",
        "yes",
        "y",
    )

    print(f"[SDT Buddy] Arguments: github_issue={github_issue_arg}, repo_link={repo_link}, include_commit_and_pr={include_commit_and_pr}")

    # Check that github_issue_arg is not None or empty
    if not github_issue_arg or not github_issue_arg.strip():
        print("Error: --github_issue argument must not be empty.")
        sys.exit(1)

    if os.path.isfile(github_issue_arg):
        with open(github_issue_arg, "r", encoding="utf-8") as f:
            github_issue_content = f.read()
    else:
        # Check if it's a number and must be > 0
        try:
            issue_num = int(github_issue_arg)
            if issue_num <= 0:
                print("Error: Issue number must be greater than 0.")
                sys.exit(1)
        except ValueError:
            pass  # Not a number, treat as string
        github_issue_content = github_issue_arg

    general_coding_project_state = WorkflowState(
        github_issue=github_issue_content,
        project_context=ProjectContext(
            repo_link=repo_link,
            language="Python",
        ),
        plan=None,
        code_changes=None,
        test_results=None,
    )
    agent = SDTAgent()
    agent.run_workflow(
        general_coding_project_state, include_commit_and_pr=include_commit_and_pr
    )
