import sys
from pathlib import Path

import cv2
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def samples_dir() -> Path:
    return ROOT / "samples"


@pytest.fixture
def load_sample(samples_dir):
    def _load(name: str):
        p = samples_dir / name
        if not p.exists():
            pytest.fail(f"No existe la sample {p}")
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            pytest.fail(f"cv2 no pudo leer {p}")
        return img
    return _load
