"""
ML Service - loads and uses trained ML models for intent classification and complexity scoring.
"""
import os
import logging
import numpy as np
from typing import Dict, Any, Optional, List
from scipy.sparse import hstack
import joblib

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')


class MLIntentClassifier:
    """ML-based intent classifier."""

    def __init__(self):
        self.model = None
        self.tfidf = None
        self.label_encoder = None
        self._load_models()

    def _load_models(self):
        """Load trained models from disk."""
        try:
            self.model = joblib.load(os.path.join(MODEL_DIR, 'intent_classifier.pkl'))
            self.tfidf = joblib.load(os.path.join(MODEL_DIR, 'tfidf_vectorizer.pkl'))
            self.label_encoder = joblib.load(os.path.join(MODEL_DIR, 'label_encoder.pkl'))
            logger.info("ML Intent Classifier loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load ML Intent Classifier: {e}")
            self.model = None
            self.tfidf = None
            self.label_encoder = None

    def is_available(self) -> bool:
        """Check if model is available."""
        return self.model is not None and self.tfidf is not None and self.label_encoder is not None

    def _extract_features(self, text: str) -> Dict[str, Any]:
        """Extract features from text (same as training)."""
        text_lower = text.lower()

        features = {
            'has_top': 1 if 'top' in text_lower else 0,
            'has_highest': 1 if 'highest' in text_lower else 0,
            'has_lowest': 1 if 'lowest' in text_lower else 0,
            'has_average': 1 if any(kw in text_lower for kw in ['average', 'avg', 'mean']) else 0,
            'has_sum': 1 if any(kw in text_lower for kw in ['sum', 'total']) else 0,
            'has_count': 1 if 'count' in text_lower else 0,
            'has_group': 1 if any(kw in text_lower for kw in ['group by', 'per', 'by ', 'breakdown']) else 0,
            'has_where': 1 if any(kw in text_lower for kw in ['where', 'filter', 'having', 'only']) else 0,
            'has_order': 1 if any(kw in text_lower for kw in ['sort', 'order', 'ascending', 'descending', 'asc', 'desc']) else 0,
            'has_join': 1 if any(kw in text_lower for kw in ['join', 'with their', 'and their', 'along with', 'together with']) else 0,
            'has_trend': 1 if any(kw in text_lower for kw in ['trend', 'over time', 'time series']) else 0,
            'has_rank': 1 if any(kw in text_lower for kw in ['rank', 'ranking', 'percentile']) else 0,
            'has_duplicate': 1 if any(kw in text_lower for kw in ['duplicate', 'repeated']) else 0,
            'has_compare': 1 if any(kw in text_lower for kw in ['compare', 'versus', 'vs', 'difference']) else 0,
            'has_limit': 1 if any(c.isdigit() for c in text_lower) else 0,
            'text_length': len(text),
            'word_count': len(text.split()),
        }

        import re
        numbers = re.findall(r'\b\d+\b', text)
        features['numbers'] = [int(n) for n in numbers]
        features['max_number'] = max(features['numbers']) if features['numbers'] else 0

        return features

    def predict(self, text: str) -> Dict[str, Any]:
        """Predict intent category from natural language text."""
        if not self.is_available():
            return {'category': 'UNKNOWN', 'confidence': 0.0}

        features = self._extract_features(text)
        feature_names = [
            'has_top', 'has_highest', 'has_lowest', 'has_average', 'has_sum',
            'has_count', 'has_group', 'has_where', 'has_order', 'has_join',
            'has_trend', 'has_rank', 'has_duplicate', 'has_compare',
            'has_limit', 'text_length', 'word_count', 'max_number'
        ]

        X_struct = np.array([[features.get(f, 0) for f in feature_names]])
        X_text = self.tfidf.transform([text])
        X = hstack([X_text, X_struct])

        # Get prediction probabilities
        proba = self.model.predict_proba(X)[0]
        pred_idx = np.argmax(proba)
        confidence = float(proba[pred_idx])
        category = self.label_encoder.inverse_transform([pred_idx])[0]

        # Get all probabilities
        all_probs = {}
        for i, prob in enumerate(proba):
            cat_name = self.label_encoder.inverse_transform([i])[0]
            all_probs[cat_name] = float(prob)

        return {
            'category': category,
            'confidence': confidence,
            'all_probabilities': all_probs,
        }


class MLComplexityScorer:
    """ML-based complexity scorer."""

    def __init__(self):
        self.model = None
        self.tfidf = None
        self._load_models()

    def _load_models(self):
        """Load trained models from disk."""
        try:
            self.model = joblib.load(os.path.join(MODEL_DIR, 'complexity_scorer.pkl'))
            self.tfidf = joblib.load(os.path.join(MODEL_DIR, 'complexity_tfidf.pkl'))
            logger.info("ML Complexity Scorer loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load ML Complexity Scorer: {e}")
            self.model = None
            self.tfidf = None

    def is_available(self) -> bool:
        """Check if model is available."""
        return self.model is not None and self.tfidf is not None

    def _extract_features(self, text: str) -> Dict[str, Any]:
        """Extract features from text (same as training)."""
        text_lower = text.lower()

        features = {
            'has_top': 1 if 'top' in text_lower else 0,
            'has_highest': 1 if 'highest' in text_lower else 0,
            'has_lowest': 1 if 'lowest' in text_lower else 0,
            'has_average': 1 if any(kw in text_lower for kw in ['average', 'avg', 'mean']) else 0,
            'has_sum': 1 if any(kw in text_lower for kw in ['sum', 'total']) else 0,
            'has_count': 1 if 'count' in text_lower else 0,
            'has_group': 1 if any(kw in text_lower for kw in ['group by', 'per', 'by ', 'breakdown']) else 0,
            'has_where': 1 if any(kw in text_lower for kw in ['where', 'filter', 'having', 'only']) else 0,
            'has_order': 1 if any(kw in text_lower for kw in ['sort', 'order', 'ascending', 'descending', 'asc', 'desc']) else 0,
            'has_join': 1 if any(kw in text_lower for kw in ['join', 'with their', 'and their', 'along with', 'together with']) else 0,
            'has_trend': 1 if any(kw in text_lower for kw in ['trend', 'over time', 'time series']) else 0,
            'has_rank': 1 if any(kw in text_lower for kw in ['rank', 'ranking', 'percentile']) else 0,
            'has_duplicate': 1 if any(kw in text_lower for kw in ['duplicate', 'repeated']) else 0,
            'has_compare': 1 if any(kw in text_lower for kw in ['compare', 'versus', 'vs', 'difference']) else 0,
            'has_limit': 1 if any(c.isdigit() for c in text_lower) else 0,
            'text_length': len(text),
            'word_count': len(text.split()),
        }

        import re
        numbers = re.findall(r'\b\d+\b', text)
        features['numbers'] = [int(n) for n in numbers]
        features['max_number'] = max(features['numbers']) if features['numbers'] else 0

        return features

    def predict(self, text: str, parsed_query: Optional[Dict[str, Any]] = None) -> float:
        """Predict complexity score from natural language text and/or parsed query."""
        if not self.is_available():
            # Fallback to rule-based estimation
            return self._rule_based_complexity(text, parsed_query)

        features = self._extract_features(text)
        feature_names = [
            'has_top', 'has_highest', 'has_lowest', 'has_average', 'has_sum',
            'has_count', 'has_group', 'has_where', 'has_order', 'has_join',
            'has_trend', 'has_rank', 'has_duplicate', 'has_compare',
            'has_limit', 'text_length', 'word_count', 'max_number'
        ]

        X_struct = np.array([[features.get(f, 0) for f in feature_names]])
        X_text = self.tfidf.transform([text])
        X = hstack([X_text, X_struct])

        score = self.model.predict(X)[0]
        return float(max(0.5, score))  # minimum complexity

    def _rule_based_complexity(self, text: str, parsed_query: Optional[Dict[str, Any]] = None) -> float:
        """Rule-based complexity estimation fallback."""
        text_lower = text.lower()
        score = 1.0

        if any(kw in text_lower for kw in ['join', 'with their', 'and their']):
            score += 2.0
        if any(kw in text_lower for kw in ['group by', 'per ', 'by ']):
            score += 1.5
        if any(kw in text_lower for kw in ['average', 'sum', 'total', 'count', 'agg']):
            score += 1.0
        if 'top' in text_lower and any(c.isdigit() for c in text_lower):
            score += 1.5
        if any(kw in text_lower for kw in ['rank', 'percentile', 'window']):
            score += 2.0
        if 'subquery' in text_lower or 'select' in text_lower.lower().count('select') > 1:
            score += 2.0

        if parsed_query:
            if parsed_query.get('joins'):
                score += len(parsed_query['joins']) * 1.5
            if parsed_query.get('group_by'):
                score += len(parsed_query['group_by']) * 0.5
            if parsed_query.get('aggregations'):
                score += len(parsed_query['aggregations']) * 1.0
            if parsed_query.get('subqueries'):
                score += len(parsed_query['subqueries']) * 2.0

        return round(score, 2)


class MLService:
    """Unified ML service for intent classification and complexity scoring."""

    def __init__(self):
        self.intent_classifier = MLIntentClassifier()
        self.complexity_scorer = MLComplexityScorer()

    def classify_intent(self, text: str) -> Dict[str, Any]:
        """Classify intent using ML model with rule-based fallback."""
        if self.intent_classifier.is_available():
            return self.intent_classifier.predict(text)
        else:
            logger.warning("ML Intent Classifier not available, using rule-based fallback")
            return self._rule_based_intent(text)

    def score_complexity(self, text: str, parsed_query: Optional[Dict[str, Any]] = None) -> float:
        """Score complexity using ML model with rule-based fallback."""
        if self.complexity_scorer.is_available():
            return self.complexity_scorer.predict(text, parsed_query)
        else:
            logger.warning("ML Complexity Scorer not available, using rule-based fallback")
            return self.complexity_scorer._rule_based_complexity(text, parsed_query)

    def _rule_based_intent(self, text: str) -> Dict[str, Any]:
        """Rule-based intent classification fallback."""
        from queries.services.intent import IntentService
        intent_service = IntentService()
        # Use a dummy schema for rule-based
        schema = {'tables': {'employees': {'columns': {}}, 'departments': {'columns': {}}, 'customers': {'columns': {}}, 'purchases': {'columns': {}}, 'products': {'columns': {}}}}
        intent = intent_service._extract_rule_based(text, schema)
        return {
            'category': intent.get('category', 'RETRIEVE'),
            'confidence': 0.7,  # lower confidence for rule-based
            'all_probabilities': {intent.get('category', 'RETRIEVE'): 0.7},
        }


# Global instance
_ml_service = None


def get_ml_service() -> MLService:
    """Get or create the global ML service instance."""
    global _ml_service
    if _ml_service is None:
        _ml_service = MLService()
    return _ml_service