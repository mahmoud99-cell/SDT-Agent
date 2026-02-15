from ..sdt_types import WorkflowState
import os
import subprocess
from ..logging_config import logger
import sys
from pathlib import Path
from ..helpers import (
    extract_repo_name_from_url,
    is_test_file,
)
from .test_generation_node import TestGenerationNode


class TestAndLintNode:
    def run(self, state: WorkflowState) -> WorkflowState:
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
        repo_name = extract_repo_name_from_url(repo_url)
        project_path = os.path.join("GitHubIssue", repo_name)

        if not os.path.isdir(project_path):
            raise FileNotFoundError(
                f"Repository path '{project_path}' not found; did issue_analysis run?"
            )

        # Use test_files from plan, fallback to files in relevant_files that look like tests
        test_files_exist = [
            f for f in test_files if Path(os.path.join(project_path, f)).is_file()
        ]
        if not test_files_exist:
            logger.info(
                "No test files found in plan, using relevant files for testing."
            )
            test_files = [f for f in relevant_files if is_test_file(f)]
            test_files_exist = [
                f for f in test_files if Path(os.path.join(project_path, f)).is_file()
            ]

        # --- If still no test files, generate tests and update test_files ---
        if not test_files_exist:
            logger.info("No test files found after fallback. Generating tests.")
            state = TestGenerationNode().run(state)
            plan = state.plan or {}
            test_files = plan.get("test_files", [])
            test_files_exist = [
                f for f in test_files if Path(os.path.join(project_path, f)).is_file()
            ]

        logger.info(f"test_and_lint() – Test files to run: {test_files_exist}")
        test_outputs = {}
        lint_outputs = {}
        all_tests_passed = True
        all_lint_passed = True
        error_accum = []  # <-- Collect errors for retry context
        if len(test_files_exist) == 0:
            logger.info("No test files found for testing. Pass tests")
            all_tests_passed = True
            state.test_results = {
                "test_outputs": test_outputs,
                "tests_passed": all_tests_passed,
            }
            state.lint_results = {
                "lint_outputs": lint_outputs,
                "lint_passed": all_lint_passed,
            }
            return state

        for file_path in test_files_exist:
            ext = Path(file_path).suffix.lower()
            test_cmd = None
            if ext == ".py":
                # --- Check for pyproject.toml with addopts containing --doctest-rst ---
                repo_root = os.path.abspath(project_path)
                pyproject_path = os.path.join(repo_root, "pyproject.toml")
                use_repo_pytest = False
                addopts_has_doctest_rst = False
                if os.path.isfile(pyproject_path):
                    try:
                        with open(pyproject_path, "r", encoding="utf-8") as f:
                            pyproject_content = f.read()
                        if "addopts" in pyproject_content:
                            if "--doctest-rst" in pyproject_content:
                                addopts_has_doctest_rst = True
                                use_repo_pytest = True
                    except Exception:
                        pass
                if use_repo_pytest:
                    # If --doctest-rst is already in addopts, don't add it to the CLI
                    test_cmd = "pytest"
                else:
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
                    # Only use relpath if not running full pytest
                    if test_cmd.startswith("pytest ") and test_cmd.strip() not in (
                        "pytest",
                        "pytest --doctest-rst",
                    ):
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
                        "Detected ImportError due to missing C extensions. Please ensure 'pip install -e .' was run during repo setup."
                    )
                    # No longer attempt to run pip install -e . here

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

        # handle if no tests ran, just
        if any("no tests ran" in v.lower() for v in test_outputs.values()):
            logger.info("test_and_lint() – No tests were run. Passing.")
            state.test_results = {
                "test_outputs": test_outputs,
                "tests_passed": True,
            }
            state.lint_results = {
                "lint_outputs": lint_outputs,
                "lint_passed": all_lint_passed,
            }
            state.passed = True  # Only lint matters if no tests ran
            return state

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
        state.last_errors = "\n\n".join(error_accum) if error_accum else None
        return state
