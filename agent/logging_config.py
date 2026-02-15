import os
import logging
from datetime import datetime

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
logger = logging.getLogger("sdt-buddy")
