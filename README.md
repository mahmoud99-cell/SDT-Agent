# SDT Buddy Agent - Software Developer Twin

**Developer Twin Project**: An AI-powered autonomous agent implementing a structured pipeline approach (see [agent_workflow_graph.png](./agent_workflow_graph.png) for graph overview) for automated software development tasks. Built with LangGraph workflows and Google Gemini 2.0 Flash model.

## Project Overview

SDT (Software Developer Twin) is a research implementation exploring autonomous software development through a multi-phase LLM agent pipeline that processes GitHub issues and generates code solutions.

### Core Capabilities

- GitHub issue analysis and classification
- Automated code generation and modification
- Quality assurance through testing and linting
- Direct repository integration with commit automation

## Architecture

### Implementation Approach

The project follows the original architectural specification with a **6-phase structured pipeline**:

1. **Issue Analysis** - Clone repository and understand GitHub issue context
2. **Project Understanding** - Given the issue number/file/text to the script, fetch and parse issue content and extract technical requirements
3. **Planning** - Keyword extraction and relevant file discovery using regex, then LLM prompting to classify relevant files (`source_files`, `relevant_files`, `test_files`)
4. **Main Code Generation** - Runs `code_generation` for source files and `test_generation` for test files, depending on the issue type
5. **Test & Lint** - Quality validation (testing and linting) with retry logic (max 3 attempts)
6. **Commit & PR** - Direct Git commits to main branch

### Technology Stack

- **Workflow Engine**: LangGraph for state machine orchestration
- **LLM**: Google Gemini 2.0 Flash (temperature=0.2)
- **Integration**: GitHub API via PyGithub
- **Quality Tools**: pytest, ruff, black
- **Data Models**: Pydantic for type-safe state management

### Current vs. Planned Architecture

**Implemented**: Functional pipeline with direct file generation and basic testing  
**Original Plan Deviations**:

- No containerized execution environment (planned Docker isolation)
- No human approval checkpoints (PLAN.md validation)
- No RAG-based semantic search (TESTED - codebase indexing and run similarity search; results still needed further refinement) (using keyword matching and LLM Files Relevance prompting instead)
- No structured issue representation phase

## Setup & Usage

### Prerequisites

- Python 3.8+
- Git
- GitHub personal access token
- Google AI API key

### Quick Start

```bash
git clone https://github.com/breath24/SDT-DeveloperTwin.git
cd SDT-DeveloperTwin/main-team
./ExcuteAgent.bat
```

OR for bash (Linux):

```bash
bash ExcuteAgent.sh
```

You can also run the benchmark agent directly:

```bash
./ExcuteAgentBenchmark.bat
```

OR for bash (Linux):

```bash
bash ExcuteAgentBenchmark.sh
```

---

### Manual Setup (if `ExcuteAgent.sh` or `ExcuteAgent.bat` fails)

If the script fails, you can perform the steps manually in this repo:

1. **Change to the project directory:**

   ```bash
   cd main-team
   ```

2. **Create a virtual environment (if not already present):**

   ```bash
   python -m venv .venv
   ```

3. **Activate the virtual environment:**

   - On **Linux/macOS**:
     ```bash
     source .venv/bin/activate
     ```
   - On **Windows** (Command Prompt):
     ```cmd
     .venv\Scripts\activate
     ```
   - On **Windows** (PowerShell):
     ```powershell
     .venv\Scripts\Activate.ps1
     ```

4. **Upgrade pip and install requirements:**

   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

5. **Run the agent:**
   ```bash
   python -m agent.main_agent --github_issue 2
   ```

---

If you encounter errors at any step, please check your Python installation and ensure all dependencies are installed.

### Configuration

Create a `.env` file from template:

```env
GOOGLE_API_KEY=your_google_api_key_here
GITHUB_TOKEN=your_github_personal_access_token
GITHUB_BRANCH=main
GITHUB_BASE_BRANCH=main
```

### Running the Agent

You can run the main agent with custom GitHub issues and repository links:

```bash
python -m agent.main_agent --github_issue <issue_number|issue_file|issue_text> [--repo_link <repo_url>] [--include_commit_and_pr true|false]
```

**Arguments:**

- `--github_issue` (required): GitHub issue number (as string/int), path to a markdown file containing the issue, or the issue content itself.
- `--repo_link` (optional): GitHub repository link to use (default: SDT-Testing-Project).
- `--include_commit_and_pr` (optional): Whether to include the commit and PR phase (`true` or `false`, default: `true`).

**Examples:**

```bash
# Run with an issue number and default repo
python -m agent.main_agent --github_issue 1

# Run with a custom repo and skip commit/PR phase
python -m agent.main_agent --github_issue 2489 --repo_link https://github.com/pvlib/pvlib-python.git --include_commit_and_pr false

# Run with a local markdown file as the issue
python -m agent.main_agent --github_issue agent/issues/bug_fix_discount_calculation.md --repo_link https://github.com/SDT-DeveloperTwin/SDT-Testing-Project.git
```

## Implementation Details

### Available Implementations

- **`main_agent.py`** - Functional refactored implementation (recommended)
- **`SDT_Buddy_Class.py`** - Object-oriented encapsulation
- **`SDT_Buddy.py`** - Original proof-of-concept

### Test Cases

The project includes curated test issues in `agent/issues/`:

- `bug_fix_discount_calculation.md` - Error handling and validation
- `feature_temperature_conversion.md` - New feature implementation
- `refactor_date_formatting.md` - Code quality improvement
- `tests_email_validation.md` - Unit test generation

### State Management

```python
class WorkflowState(BaseModel):
    github_issue: str              # Issue description
    project_context: ProjectContext # Repository metadata
    plan: Optional[dict]           # Execution plan
    code_changes: Optional[dict]   # Generated modifications
    test_results: Optional[dict]   # Quality validation results
    retry_count: int = 0           # Failure retry tracking
```

## Evaluation & Benchmarking

### SWE-bench Integration

```python
from agent.benchmark_agent import SDTAgent
from datasets import load_dataset

dataset = load_dataset("princeton-nlp/SWE-bench_Lite", split="dev")
agent = SDTAgent()
```

### Running the Benchmark Agent

You can run the benchmark agent on the SWE-bench dataset (dev split total 23 samples) with a configurable number of samples. The commit_and_pr option is by default disabled:

Run agent\benchmark_agent.py (default 3 samples):

```bash
python -m agent.benchmark_agent
```

Or specify the number of samples:

```bash
python -m agent.benchmark_agent --num-samples 5
```

or using the short form:

```bash
python -m agent.benchmark_agent -n 5
```

- `--num-samples` (`-n`): Optional. Number of samples to run from the dataset. Defaults to 3 if not specified.

## Research Context

### Academic Objectives

This project explores:

- Multi-phase LLM agent architectures for software engineering
- State management in autonomous development workflows
- Quality assurance integration in AI-generated code
- Evaluation methodologies for code generation agents

### Future Enhancements

Based on original architectural specification:

- Containerized execution environments
- Human-in-the-loop approval checkpoints
- Diff/patch generation strategies
- Enhanced error handling (current implementation handles some workspace errors in a general manner)
- Memory & ReAct agent integration in the state graph

### Development Notes

- Logging available in `agent/logs/` directory
- Multiple implementation approaches for comparison
- Modular design supporting architectural evolution

## Benchmark Results

### Simple SDT-Testing-Project cases:

The agent was able to handle the 3 main simple issues (Bug, Refactor, Test Generation). To see the results, check the commits ([SDT-Testing-Project commits](https://github.com/SDT-DeveloperTwin/SDT-Testing-Project/commits/main/)) and the log files (`agent_run_output_20250804_xxxx`). Not all passed on the first attempt.

### Example 1: pvlib-python Issue ([agent_run_output_20250808_1850.txt](./agent/logs/agent_run_output_20250808_1850.txt))

**Summary:**  
The agent successfully processed a documentation-related issue in the `pvlib-python` repository. It identified relevant files, generated code changes, and ran linting and tests. However, the test files were actually example scripts, so `pytest` found no tests to run. Linting passed, and the workflow completed successfully. This does not mean the pipeline failed, as the agent still did the task in hand.

**Analysis:**

- **Strengths:**
  - Correctly identified and modified relevant files.
  - Passed linting and did not introduce syntax errors.
  - Generated the needed code.
  - Pipeline completed without errors.
- **Observations:**
  - Some runs, the agent treated example scripts as test files, but these did not contain actual tests, so no validation of code changes occurred. (Fixed by prompt ✓)

---

### Example 2: astropy Issue ([agent_run_output_20250808_1934.txt](./agent/logs/agent_run_output_20250808_1934.txt))

**Summary:**  
The agent attempted to process a modeling bug in the `astropy` repository. It set up the environment and installed dependencies but failed to identify any candidate files for modification or testing. As a result, no code changes or tests were performed.

**Analysis:**

- **Strengths:**
  - Correctly set up the environment and dependencies.
  - Parsed the issue and project context.
  - Gracefully closed the run after 3 attempts and looping the error.
- **Limitations:**
  - Some runs failed to identify relevant source or test files (very rare, added condition to handle this case ✓)
  - Mainly failed due to complex workspace env setup (it's handled automatically by the agent but sometimes needs human input)

**Improvement Suggestions:**

- Use a more capable LLM or integrate code search tools (e.g., embedding-based RAG) to better identify relevant files.
- Implement a ReAct agent or similar iterative reasoning loop to refine file selection and planning.
- Add fallback strategies (e.g., prompt for human input or retry with broader search) when no files are found.

---

### General Recommendations

- **Better LLMs:** Upgrading to a more advanced LLM can improve understanding of complex issues and codebases.
- **Semantic Search:** Integrate RAG or embedding-based search to improve relevant file discovery.
- **ReAct Agent:** Use a ReAct-style agent for iterative planning, reasoning, and self-correction.
- **Test Detection:** Enhance logic to distinguish between example scripts and real tests, and generate tests if missing.
- **Human-in-the-Loop:** Optionally prompt for human guidance when the agent is uncertain or fails to find relevant files.

---

_See `agent/logs/` for full logs and details of each benchmark run._

---

## Contributors

- Mahmoud Hemida
- Eslam Elkasabi
- Nouran Ayman
- Pritesh Soni

Thank you to all contributors and supervisor for your support!

