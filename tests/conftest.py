import importlib.util
import os
from pathlib import Path

import pytest

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"

LAMBDA_DIR = Path(__file__).resolve().parents[1] / "stack" / "lambdas"


@pytest.fixture
def load_lambda(monkeypatch):
    def load(directory, filename="lambda_function.py"):
        path = LAMBDA_DIR / directory
        monkeypatch.syspath_prepend(str(path))
        spec = importlib.util.spec_from_file_location(directory.replace("-", "_"), path / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load


@pytest.fixture
def video_processor(load_lambda):
    return load_lambda("rekopoc-apply-faces-to-video-docker", "video_processor.py")
