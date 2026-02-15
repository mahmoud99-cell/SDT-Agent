"""Helper functions for SDT agent"""

from .sdt_types import WorkflowState
from .logging_config import logger
import os
import subprocess
import sys
from pathlib import Path
import re


def ensure_test_lint_dependencies(dependencies: list[str], project_type: str):
    logger.info("=" * 20 + " starting ensure_test_lint_dependencies phase " + "=" * 20)
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
                subprocess.run([python_exe, "-m", "pip", "install", dep], check=True)
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


def format_instruction_prompt(task: str, context: str) -> str:
    return (
        "You are a senior software engineer.\n"
        "Respond ONLY with valid, runnable code for the project's tech stack. "
        "Do NOT wrap the code in markdown fences. "
        "Do NOT include test functions, test cases, or usage examples unless explicitly requested. "
        "Ensure the code is idiomatic and follows best practices for the relevant language and tools.\n\n"
        f"Task:\n{task}\n\nContext:\n{context}"
    )


def get_code_generation_prompt(language: str) -> str:
    lang_str = f"{language.capitalize()} " if language else ""
    return (
        f"Implement the following GitHub issue in the {lang_str}source file provided.\n"
        "- Output the full, valid code for the file (not just a section).\n"
        "- Do NOT include markdown code fences in the response.\n"
        "- Do NOT create or modify test functions, test cases, or usage examples unless explicitly requested.\n"
        "- Maintain correctness and code quality according to the project's tech stack and tools.\n"
    )


def get_test_generation_prompt(language: str) -> str:
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


def code_header_remover(raw_code: str) -> str:
    """
    Removes markdown code fences and leading/trailing whitespace from code.
    """
    code = raw_code.strip()
    code = re.sub(r"^```[a-zA-Z]*\s*|```$", "", code, flags=re.MULTILINE).strip()
    return code


def is_issue_number(issue_str: str) -> bool:
    """
    Returns True if the string represents an integer (issue number), False otherwise.
    """
    try:
        int(issue_str)
        return True
    except Exception:
        return False


def get_test_dir(project_path):
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


def is_test_file(f):
    name = Path(f).name.lower()
    return "test" in name or "spec" in name


def detect_language_from_context(state: WorkflowState) -> str:
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



def extract_repo_name_from_url(url: str) -> str:
    """
    Extracts the 'owner/repo' part from a GitHub repository URL.
    Supports both HTTPS and SSH URLs.
    """
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith("git@"):
        # SSH URL: git@github.com:owner/repo
        return url.split(":", 1)[-1]
    elif url.startswith("https://") or url.startswith("http://"):
        # HTTPS URL: https://github.com/owner/repo
        parts = url.split("/")
        return "/".join(parts[-2:])
    else:
        raise ValueError(f"Unrecognized GitHub repo URL format: {url}")