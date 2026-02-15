from ..sdt_types import WorkflowState
from ..logging_config import logger
from .code_generation_node import CodeGenerationNode
from .test_generation_node import TestGenerationNode

class MainCodeGenerationNode:
    def run(self, state: WorkflowState) -> WorkflowState:
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
                state = TestGenerationNode().run(state)
        else:
            if source_files:
                logger.info(
                    "main_code_generation: Detected code issue. Running code_generation for source files."
                )
                state = CodeGenerationNode().run(state)
            # if test_files: # Uncomment this block if you want to run test_generation for test files after code_generation,
            #     # we assume that issue is only 1 type not both
            #     logger.info("main_code_generation: Also running test_generation for test files.")
            #     state = TestGenerationNode().run(state)
        return state
                

