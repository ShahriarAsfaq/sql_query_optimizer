"""
Train ML models for intent classification and complexity scoring.
"""
import os
import json
import pickle
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Tuple
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report, accuracy_score, mean_squared_error
from scipy.sparse import hstack
import joblib


MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
os.makedirs(MODEL_DIR, exist_ok=True)


def load_dataset(filepath: str) -> List[Dict[str, Any]]:
    """Load dataset from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)


def prepare_intent_features(samples: List[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray, TfidfVectorizer, LabelEncoder]:
    """Prepare features for intent classification."""
    # Text features using TF-IDF
    texts = [s['natural_language'] for s in samples]
    tfidf = TfidfVectorizer(max_features=500, ngram_range=(1, 2), stop_words='english')
    X_text = tfidf.fit_transform(texts)

    # Structured features
    feature_names = [
        'has_top', 'has_highest', 'has_lowest', 'has_average', 'has_sum',
        'has_count', 'has_group', 'has_where', 'has_order', 'has_join',
        'has_trend', 'has_rank', 'has_duplicate', 'has_compare',
        'has_limit', 'text_length', 'word_count', 'max_number'
    ]

    X_struct = np.array([
        [s['features'].get(f, 0) for f in feature_names]
        for s in samples
    ])

    # Combine features
    X = hstack([X_text, X_struct])

    # Labels
    labels = [s['intent_category'] for s in samples]
    le = LabelEncoder()
    y = le.fit_transform(labels)

    return X, y, tfidf, le


def train_intent_classifier(samples: List[Dict[str, Any]], test_size: float = 0.2) -> Tuple[Any, TfidfVectorizer, LabelEncoder, Dict]:
    """Train intent classifier model."""
    print("Preparing features for intent classification...")
    X, y, tfidf, le = prepare_intent_features(samples)

    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42, stratify=y
    )

    print(f"Training on {X_train.shape[0]} samples, testing on {X_test.shape[0]} samples")

    # Train Random Forest classifier
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=20,
        min_samples_split=5,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
        class_weight='balanced'
    )

    clf.fit(X_train, y_train)

    # Evaluate
    y_pred = clf.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)

    print(f"Intent Classifier Accuracy: {accuracy:.4f}")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=le.classes_))

    # Feature importance
    feature_names = list(tfidf.get_feature_names_out()) + [
        'has_top', 'has_highest', 'has_lowest', 'has_average', 'has_sum',
        'has_count', 'has_group', 'has_where', 'has_order', 'has_join',
        'has_trend', 'has_rank', 'has_duplicate', 'has_compare',
        'has_limit', 'text_length', 'word_count', 'max_number'
    ]
    importances = clf.feature_importances_
    top_features = sorted(zip(feature_names, importances), key=lambda x: x[1], reverse=True)[:20]
    print("\nTop 20 Features:")
    for feat, imp in top_features:
        print(f"  {feat}: {imp:.4f}")

    # Save model
    joblib.dump(clf, os.path.join(MODEL_DIR, 'intent_classifier.pkl'))
    joblib.dump(tfidf, os.path.join(MODEL_DIR, 'tfidf_vectorizer.pkl'))
    joblib.dump(le, os.path.join(MODEL_DIR, 'label_encoder.pkl'))

    return clf, tfidf, le, {
        'accuracy': accuracy,
        'feature_importance': dict(top_features),
        'n_samples': len(samples),
    }


def prepare_complexity_features(samples: List[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray, TfidfVectorizer]:
    """Prepare features for complexity scoring."""
    # For complexity, we'll generate synthetic complexity scores based on query structure
    # In production, these would come from actual EXPLAIN plans

    texts = [s['natural_language'] for s in samples]
    tfidf = TfidfVectorizer(max_features=300, ngram_range=(1, 2), stop_words='english')
    X_text = tfidf.fit_transform(texts)

    # Structured features that correlate with query complexity
    feature_names = [
        'has_top', 'has_highest', 'has_lowest', 'has_average', 'has_sum',
        'has_count', 'has_group', 'has_where', 'has_order', 'has_join',
        'has_trend', 'has_rank', 'has_duplicate', 'has_compare',
        'has_limit', 'text_length', 'word_count', 'max_number'
    ]

    X_struct = np.array([
        [s['features'].get(f, 0) for f in feature_names]
        for s in samples
    ])

    # Generate synthetic complexity targets
    # Higher complexity for: JOIN, GROUP, AGGREGATE, TOP_N with aggregate, subqueries
    # Lower for: simple RETRIEVE, FILTER
    complexity_map = {
        'RETRIEVE': 1.0,
        'FILTER': 1.5,
        'SORT': 2.0,
        'AGGREGATE': 3.0,
        'GROUP': 3.0,
        'TOP_N': 3.5,
        'JOIN': 4.0,
        'TREND': 4.0,
        'RANKING': 4.5,
        'DUPLICATE_DETECTION': 2.5,
        'COMPARISON': 4.0,
    }

    # Add some noise based on features
    y = []
    for s in samples:
        base = complexity_map.get(s['intent_category'], 2.0)
        # Add complexity for specific features
        if s['features'].get('has_join'):
            base += 1.0
        if s['features'].get('has_group'):
            base += 0.5
        if s['features'].get('has_average') or s['features'].get('has_sum'):
            base += 0.5
        if s['features'].get('has_top') and s['features'].get('max_number', 0) > 10:
            base += 0.5
        # Add small noise
        base += np.random.normal(0, 0.2)
        y.append(max(0.5, base))  # minimum complexity

    X = hstack([X_text, X_struct])
    y = np.array(y)

    return X, y, tfidf


def train_complexity_scorer(samples: List[Dict[str, Any]], test_size: float = 0.2) -> Tuple[Any, TfidfVectorizer, Dict]:
    """Train complexity scorer model."""
    print("Preparing features for complexity scoring...")
    X, y, tfidf = prepare_complexity_features(samples)

    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42
    )

    print(f"Training on {X_train.shape[0]} samples, testing on {X_test.shape[0]} samples")

    # Train Random Forest regressor
    reg = RandomForestRegressor(
        n_estimators=200,
        max_depth=15,
        min_samples_split=5,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1
    )

    reg.fit(X_train, y_train)

    # Evaluate
    y_pred = reg.predict(X_test)
    mse = mean_squared_error(y_test, y_pred)
    rmse = np.sqrt(mse)

    print(f"Complexity Scorer RMSE: {rmse:.4f}")
    print(f"Complexity Scorer MSE: {mse:.4f}")

    # Feature importance
    feature_names = list(tfidf.get_feature_names_out()) + [
        'has_top', 'has_highest', 'has_lowest', 'has_average', 'has_sum',
        'has_count', 'has_group', 'has_where', 'has_order', 'has_join',
        'has_trend', 'has_rank', 'has_duplicate', 'has_compare',
        'has_limit', 'text_length', 'word_count', 'max_number'
    ]
    importances = reg.feature_importances_
    top_features = sorted(zip(feature_names, importances), key=lambda x: x[1], reverse=True)[:20]
    print("\nTop 20 Features:")
    for feat, imp in top_features:
        print(f"  {feat}: {imp:.4f}")

    # Save model
    joblib.dump(reg, os.path.join(MODEL_DIR, 'complexity_scorer.pkl'))
    joblib.dump(tfidf, os.path.join(MODEL_DIR, 'complexity_tfidf.pkl'))

    return reg, tfidf, {
        'rmse': rmse,
        'mse': mse,
        'feature_importance': dict(top_features),
        'n_samples': len(samples),
    }


def evaluate_models(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Evaluate both models on test data."""
    print("=" * 60)
    print("EVALUATING INTENT CLASSIFIER")
    print("=" * 60)
    clf, tfidf_intent, le, intent_metrics = train_intent_classifier(samples)

    print("\n" + "=" * 60)
    print("EVALUATING COMPLEXITY SCORER")
    print("=" * 60)
    reg, tfidf_complexity, complexity_metrics = train_complexity_scorer(samples)

    return {
        'intent_classifier': intent_metrics,
        'complexity_scorer': complexity_metrics,
    }


if __name__ == '__main__':
    print("Loading synthetic dataset...")
    dataset_path = os.path.join(os.path.dirname(__file__), 'synthetic_dataset.json')
    samples = load_dataset(dataset_path)
    print(f"Loaded {len(samples)} samples")

    print("\nTraining models...")
    metrics = evaluate_models(samples)

    print("\n" + "=" * 60)
    print("TRAINING COMPLETE")
    print("=" * 60)
    print(f"Intent Classifier Accuracy: {metrics['intent_classifier']['accuracy']:.4f}")
    print(f"Complexity Scorer RMSE: {metrics['complexity_scorer']['rmse']:.4f}")
    print(f"\nModels saved to {MODEL_DIR}")