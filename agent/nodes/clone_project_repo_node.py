from ..sdt_types import WorkflowState
import os
import subprocess
from ..logging_config import logger
from pathlib import Path
from ..helpers import ensure_test_lint_dependencies, extract_repo_name_from_url
import sys


class CloneProjectRepoNode:
    def run(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting initialize_issue_repo phase " + "=" * 20)
        logger.info("initialize_issue_repo() – Cloning GitHub repository...")
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            and getattr(state.project_context, "repo_link", None)
            else "https://github.com/mahmoud99-cell/SDT-Testing-Project.git"
        )
        repo_name = extract_repo_name_from_url(repo_url)
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

        # --- Environment setup: install dependencies and editable install if needed ---
        if state.project_context and hasattr(state.project_context, "dependencies"):
            dependencies = state.project_context.dependencies
            if dependencies is not None:
                if isinstance(dependencies, str):
                    dependencies_list = [
                        dep.strip() for dep in dependencies.split(",") if dep.strip()
                    ]
                else:
                    dependencies_list = list(dependencies)
                ensure_test_lint_dependencies(
                    dependencies_list, state.project_context.language
                )
        # Install requirements.txt if it exists
        repo_root = os.path.abspath(destination_dir)
        logger.info(f"Looking for requirements.txt/pyproject.toml in {repo_root}")
        logger.info(f"Directory contents: {os.listdir(repo_root)}")
        requirements_path = os.path.join(repo_root, "requirements.txt")
        if os.path.isfile(requirements_path):
            logger.info(
                "Detected requirements.txt, running 'pip install -r requirements.txt'"
            )
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                    cwd=repo_root,
                )
                logger.info("Successfully installed requirements from requirements.txt")
            except Exception as e:
                logger.error(f"Failed to install requirements.txt: {e}")
        else:
            logger.info(
                "No requirements.txt found, skipping 'pip install -r requirements.txt'"
            )

        # Install editable if pyproject.toml or setup.py exists
        pyproject_path = os.path.join(repo_root, "pyproject.toml")
        setup_py_path = os.path.join(repo_root, "setup.py")
        if os.path.isfile(pyproject_path) or os.path.isfile(setup_py_path):
            logger.info(
                "Detected pyproject.toml or setup.py, running 'pip install -e .'"
            )
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "-e", "."],
                    cwd=repo_root,
                )
                logger.info("Successfully ran 'pip install -e .'")
            except Exception as e:
                logger.error(f"Failed to run 'pip install -e .': {e}")
        else:
            logger.info(
                "No pyproject.toml or setup.py found, skipping 'pip install -e .'"
            )

        return state
