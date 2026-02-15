import os
import sys
import json
import traceback
from dotenv import load_dotenv
from pydantic import BaseModel
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
import argparse  # <-- Add this import
import warnings

# Main agent class for SDT workflow
from .nodes.code_generation_node import CodeGenerationNode
from .nodes.test_generation_node import TestGenerationNode
from .nodes.clone_project_repo_node import CloneProjectRepoNode
from .nodes.retrieve_issue_details_node import RetrieveIssueDetailsNode
from .nodes.planning_node import PlanningNode
from .nodes.test_and_lint_node import TestAndLintNode
from .nodes.commit_and_pr_node import CommitAndPRNode
from .sdt_types import WorkflowState, ProjectContext

warnings.filterwarnings("ignore")

# --- Logging setup ---
from .logging_config import logger


class SDTAgent:
    def __init__(self):
        # --- Environment setup ---
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        os.chdir(project_root)
        load_dotenv()

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
        self.config = {"configurable": {"thread_id": "1"}}

        self.code_generation_node = CodeGenerationNode(self.agent_respond)
        self.test_generation_node = TestGenerationNode(self.agent_respond)
        self.clone_project_repo_node = CloneProjectRepoNode()
        self.retrieve_issue_details_node = RetrieveIssueDetailsNode()
        self.planning_node = PlanningNode(self.agent_respond)
        self.test_and_lint_node = TestAndLintNode()
        self.commit_and_pr_node = CommitAndPRNode(self.agent_respond)

    def should_retry(self, state: WorkflowState, include_commit_and_pr: bool):
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
                state.last_errors = None  # Clear errors after giving up
                return END
        else:
            logger.info("Tests passed or not applicable. Proceeding to commit_and_pr.")
            state.last_errors = None  # Clear errors on success
        if include_commit_and_pr:
            return (
                "commit_and_pr"
                if state.test_results and state.test_results.get("tests_passed")
                else END
            )
        else:
            return END

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

    def build_phases(self, include_commit_and_pr: bool = True):
        phases = [
            ("issue_analysis", self.clone_project_repo_node.run),
            ("project_understanding", self.retrieve_issue_details_node.run),
            ("planning", self.planning_node.run),
            ("main_code_generation", self.code_generation_node.run),
            ("test_and_lint", self.test_and_lint_node.run),
        ]
        if include_commit_and_pr:
            phases.append(("commit_and_pr", self.commit_and_pr_node.run))
        return phases

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
    # Usage:
    # python -m agent.main_agent --github_issue <issue_number|issue_file|issue_text> [--repo_link <repo_url>] [--include_commit_and_pr true|false]
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

    print(
        f"[SDT Buddy] Arguments: github_issue={github_issue_arg}, repo_link={repo_link}, include_commit_and_pr={include_commit_and_pr}"
    )

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
    # https://github.com/pvlib/pvlib-python/issues/2489
    # python -m agent.main_agent --github_issue 2489 --repo_link https://github.com/pvlib/pvlib-python.git --include_commit_and_pr false
