from ..sdt_types import WorkflowState
import os
from github import Github
from github.InputGitTreeElement import InputGitTreeElement
from ..logging_config import logger
from helpers import (
    extract_repo_name_from_url
)

class CommitAndPRNode:
    def __init__(self, agent_respond):
        self.agent_respond = agent_respond

    def run(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting commit_and_pr phase " + "=" * 20)
        logger.info("commit_and_pr() – Starting commit_and_pr phase...")
        repo_url = (
            state.project_context.repo_link
            if state.project_context
            and getattr(state.project_context, "repo_link", None)
            else "https://github.com/mahmoud99-cell/SDT-Testing-Project.git"
        )
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
                repo_name = extract_repo_name_from_url(repo_url)
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
            repo_name = extract_repo_name_from_url(repo_url)
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
