import sys
from pathlib import Path

# Add the root directory to sys.path so that 'api' can be imported
root = Path(__file__).parent.parent
sys.path.insert(0, str(root))
