from ..sdt_types import WorkflowState
import os
from ..logging_config import logger
from github import Github
import strip_markdown
from ..helpers import is_issue_number,extract_repo_name_from_url
class RetrieveIssueDetailsNode:
    def run(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 20 + " starting retrieve_issue_details phase " + "=" * 20)
        logger.info(
            "retrieve_issue_details() – Loading issue from state.github_issue..."
        )
        # Check if github_issue is a number (as string), fetch from GitHub if so
        if state.github_issue and is_issue_number(state.github_issue):
            issue_number = int(state.github_issue)
            github_token = os.getenv("GITHUB_TOKEN")
            repo_name = None
            if state.project_context and state.project_context.repo_link:
                repo_name = extract_repo_name_from_url(state.project_context.repo_link)
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
