import sys
from pathlib import Path

# scripts/ is a folder of standalone tools, not a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
