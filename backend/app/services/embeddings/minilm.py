"""Local sentence embeddings with ONNX Runtime (ADR 0012).

Reproduces what ``sentence-transformers`` does for all-MiniLM-L6-v2, without
PyTorch:

    text ─► WordPiece tokenizer (truncate at 256) ─► BERT (ONNX) ─► last_hidden_state
         ─► mean over real tokens (attention mask) ─► L2 normalise ─► 384 floats

The three model modules (Transformer, mean Pooling, Normalize) are read from the
model's own ``modules.json`` and ``1_Pooling/config.json``. The files are the
official ONNX export published in the model repository, fetched at a pinned
revision and cached under ``~/.cache/huggingface``.

Inference is CPU-bound, so the async methods run it in a worker thread and the
event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.logging import get_logger
from app.services.embeddings.base import EmbeddingUnavailableError

logger = get_logger(__name__)

_MODEL_FILE = "onnx/model.onnx"
_TOKENIZER_FILE = "tokenizer.json"
# From the model's sentence_bert_config.json. tokenizer.json itself says 128;
# sentence-transformers overrides it with this value, so we do the same.
_MAX_SEQ_LENGTH = 256


@dataclass(frozen=True, slots=True)
class _Loaded:
    session: object  # onnxruntime.InferenceSession
    input_names: frozenset[str]
    tokenizer: object  # tokenizers.Tokenizer, truncating + padding
    counter: object  # tokenizers.Tokenizer, untouched, for exact counts


class MiniLMEmbedder:
    dimensions = 384
    max_tokens = _MAX_SEQ_LENGTH

    def __init__(self, *, model_id: str, revision: str, batch_size: int = 32) -> None:
        self._model_id = model_id
        self._revision = revision
        self._batch_size = batch_size
        self.model = f"{model_id}@{revision[:12]}"
        self._loaded: _Loaded | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _fetch(self, filename: str) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(self._model_id, filename, revision=self._revision))

    def _load(self) -> _Loaded:
        # Double-checked: many threads may ask at once; only one loads.
        if self._loaded is not None:
            return self._loaded
        with self._lock:
            if self._loaded is not None:
                return self._loaded
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                model_path = self._fetch(_MODEL_FILE)
                tokenizer_path = self._fetch(_TOKENIZER_FILE)

                tokenizer = Tokenizer.from_file(str(tokenizer_path))
                tokenizer.enable_truncation(max_length=_MAX_SEQ_LENGTH)
                pad_id = tokenizer.token_to_id("[PAD]")
                tokenizer.enable_padding(pad_id=pad_id, pad_token="[PAD]")
                counter = Tokenizer.from_file(str(tokenizer_path))
                counter.no_truncation()
                counter.no_padding()

                options = ort.SessionOptions()
                options.log_severity_level = 3  # errors only
                session = ort.InferenceSession(
                    str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
                )
            except Exception as exc:
                logger.exception("embedding model failed to load", extra={"model": self.model})
                raise EmbeddingUnavailableError() from exc

            self._loaded = _Loaded(
                session=session,
                input_names=frozenset(i.name for i in session.get_inputs()),
                tokenizer=tokenizer,
                counter=counter,
            )
            logger.info("embedding model loaded", extra={"model": self.model})
            return self._loaded

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------

    def count_tokens(self, texts: list[str]) -> list[int]:
        if not texts:
            return []
        counter = self._load().counter
        return [len(e.ids) for e in counter.encode_batch(texts, add_special_tokens=False)]

    def embed_sync(self, texts: list[str]) -> np.ndarray:
        loaded = self._load()
        if not texts:
            return np.zeros((0, self.dimensions), dtype=np.float32)
        out = []
        for start in range(0, len(texts), self._batch_size):
            out.append(self._embed_batch(loaded, texts[start : start + self._batch_size]))
        return np.vstack(out)

    def _embed_batch(self, loaded: _Loaded, texts: list[str]) -> np.ndarray:
        encodings = loaded.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in loaded.input_names:
            feed["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)

        (hidden,) = loaded.session.run(["last_hidden_state"], feed)
        # Mean pooling: average token vectors, ignoring padding.
        weights = mask[:, :, None].astype(np.float32)
        summed = (hidden * weights).sum(axis=1)
        counts = np.clip(weights.sum(axis=1), 1e-9, None)
        pooled = summed / counts
        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
        return (pooled / norms).astype(np.float32)

    def health_check(self) -> tuple[bool, str]:
        if self._loaded is not None:
            return True, f"loaded (model={self.model})"
        from huggingface_hub import try_to_load_from_cache

        cached = all(
            isinstance(try_to_load_from_cache(self._model_id, f, revision=self._revision), str)
            for f in (_MODEL_FILE, _TOKENIZER_FILE)
        )
        # Not being downloaded yet is not a failure: it happens on first use.
        state = (
            "cached, loads on first use" if cached else "not downloaded yet, fetched on first use"
        )
        return True, f"{state} (model={self.model})"

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = await asyncio.to_thread(self.embed_sync, texts)
        return vectors.tolist()

    async def embed_query(self, text: str) -> list[float]:
        vectors = await asyncio.to_thread(self.embed_sync, [text])
        return vectors[0].tolist()
