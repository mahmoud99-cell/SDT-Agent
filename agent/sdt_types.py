from typing import Optional, List, TypedDict
from pydantic import BaseModel, Field

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
    lint_results: Optional[LintResultsModel] = None
    passed: Optional[bool] = None
    last_errors: Optional[str] = None