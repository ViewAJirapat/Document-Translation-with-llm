import os
import sys
import tempfile
from pathlib import Path

# config reads DATA_DIR at import time, so point it at a throwaway directory before anything imports app.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="lltrans-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
