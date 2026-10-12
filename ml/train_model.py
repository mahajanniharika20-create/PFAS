"""Train and evaluate a Random Forest suitability-score regressor."""
import json
import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ml.config import (
    DATASET_PATH, MODEL_PATH, METRICS_PATH, DATA_DIR, ARTIFACT_DIR,
    REPORT_DIR, NUMERIC_FEATURES, CATEGORICAL_FEATURES, FEATURE_COLUMNS,
    TARGET_COLUMN, RANDOM_STATE,
)

def main():
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Dataset not found at {DATASET_PATH}. Run: python ml/generate_synthetic_data.py"
        )
    df = pd.read_csv(DATASET_PATH)
    required = FEATURE_COLUMNS + [TARGET_COLUMN]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Dataset is missing columns: {missing}")

    X = df[FEATURE_COLUMNS]
    y = df[TARGET_COLUMN]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_STATE
    )

    preprocessor = ColumnTransformer([
        ("numeric", "passthrough", NUMERIC_FEATURES),
        ("categorical", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
    ])
    pipeline = Pipeline([
        ("preprocessor", preprocessor),
        ("model", RandomForestRegressor(
            n_estimators=250, random_state=RANDOM_STATE, min_samples_leaf=2, n_jobs=-1
        )),
    ])
    pipeline.fit(X_train, y_train)
    predictions = pipeline.predict(X_test)

    metrics = {
        "model": "RandomForestRegressor",
        "dataset": DATASET_PATH.name,
        "rows": int(len(df)),
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "mae": round(float(mean_absolute_error(y_test, predictions)), 4),
        "rmse": round(float(mean_squared_error(y_test, predictions) ** 0.5), 4),
        "r2": round(float(r2_score(y_test, predictions)), 4),
        "warning": "Synthetic data and synthetic target formula; metrics do not establish real-world performance.",
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, MODEL_PATH)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print("Model saved:", MODEL_PATH)
    print(json.dumps(metrics, indent=2))

if __name__ == "__main__":
    main()
