"""
Incident categorization service.

Loads the trained scikit-learn model once at import time and exposes
a single  categorize(text)  function used when a ticket is created.
"""
import os
import joblib

from app.config import settings

# Module-level cache — model loads once, reused for every request.
_model = None


def _load_model():
    global _model
    if _model is None:
        path = settings.ML_MODEL_PATH
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Categorizer model not found at '{path}'. "
                f"Run:  python scripts/train_categorizer.py"
            )
        _model = joblib.load(path)
    return _model


def categorize(text: str) -> tuple[str | None, float]:
    """
    Predict the category for a ticket's text.

    Returns (category, confidence).

    If confidence is below ML_CONFIDENCE_THRESHOLD, category is returned
    as None so the caller can flag it for manual review instead of
    trusting a low-confidence guess.
    """
    if not text or not text.strip():
        return None, 0.0

    model = _load_model()

    # Predicted label + probability of that label
    probabilities = model.predict_proba([text])[0]
    classes = model.classes_
    best_idx = probabilities.argmax()

    category = str(classes[best_idx])
    confidence = float(probabilities[best_idx])

    if confidence < settings.ML_CONFIDENCE_THRESHOLD:
        # Not confident enough — let a human decide.
        return None, confidence

    return category, confidence


def is_model_available() -> bool:
    """Used by a health/status endpoint to check if the model is loaded."""
    try:
        _load_model()
        return True
    except FileNotFoundError:
        return False
