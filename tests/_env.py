"""Every test module imports this first. The tests then import claude_focus
from this checkout and keep state, config and logs in a scratch folder
(CLAUDE_FOCUS_DIR), never in the real ~/.claude-focus; the folder is removed
at exit."""
import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "CLAUDE_FOCUS_DIR" not in os.environ:
    os.environ["CLAUDE_FOCUS_DIR"] = tempfile.mkdtemp(prefix="claude-focus-tests-")
    atexit.register(shutil.rmtree, os.environ["CLAUDE_FOCUS_DIR"], True)
