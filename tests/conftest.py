from pathlib import Path

import pytest
from PIL import Image

from photocurate.media import index_corpus
from photocurate.provider import DEFAULT_PROMPT, DEFAULT_SCHEMA
from photocurate.store import Store


@pytest.fixture
def store(tmp_path):
    state = Store(tmp_path / "state")
    yield state
    state.close()


@pytest.fixture
def corpus(tmp_path) -> Path:
    root = tmp_path / "photos"
    (root / "nested").mkdir(parents=True)
    for i in range(12):
        path = root / "nested" / f"photo-{i:02}.jpg"
        Image.new("RGB", (800, 400), (i * 20, 100, 150)).save(path)
    return root


@pytest.fixture
def indexed(store, corpus):
    index_corpus(store, corpus, progress=lambda _: None)
    return corpus


@pytest.fixture
def config():
    return {
        "model": "example/vision-model",
        "prompt": DEFAULT_PROMPT,
        "schema": DEFAULT_SCHEMA,
        "score_field": "album_score",
        "proxy_size": 512,
        "temperature": 0,
        "max_tokens": 512,
        "timeout_seconds": 2,
        "concurrency": 1,
        "input_usd_per_million": None,
        "output_usd_per_million": None,
    }
