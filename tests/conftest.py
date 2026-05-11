import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("DB_PATH", _tmp.name)
sys.path.insert(0, str(Path(__file__).parent.parent / "website"))
