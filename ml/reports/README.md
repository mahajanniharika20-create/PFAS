# PFAS Recommendation & ML Evaluation Report

## 1. Current Live Architecture: Rule-Based Recommendation Engine

The live recommendation module (`ml/engine.py`) operates as a **transparent, rule-based recommendation and allocation engine**.
- **Not a Black-Box ML Model:** The live recommendation pipeline does not load serialized model artifacts (`.joblib`) or synthetic datasets.
- **Initial Policy Weights:** The scoring weights (Domain Match 40%, Subject Preference 30%, Slot Preference 15%, Capacity Balance 15%) represent initial institutional policy choices, not empirical parameters learned from machine learning models.
- **Evidence-Based Eligibility:** Candidates with unverified information (e.g. unknown workload cap, unconfirmed availability, unverified qualifications) are strictly classified as `NEEDS_REVIEW` and never represented as confirmed eligible.

---

## 2. Historical Baseline: Synthetic Demonstration Artifacts

The repository preserves historical demonstration assets for baseline reproducibility:
- `ml/data/synthetic_faculty_subject_suitability.csv`: 6,000 synthetic records generated from an artificial formula with Gaussian noise.
- `ml/artifacts/suitability_pipeline.joblib`: Random Forest Regressor trained on the synthetic dataset.
- `ml/reports/evaluation_metrics.json`: Baseline metrics (MAE ~10.8, RMSE ~13.4, R² ~0.52).

> **Important Caveat:** These metrics measure how closely the Random Forest learned the developer's synthetic formula. They do **not** reflect real-world allocation accuracy or educational performance. The live allocation engine is strictly decoupled from these assets.

---

## 3. Institutional Requirements for Future ML Model Training

To transition from the initial rule-based policy engine to a genuinely trained and evaluated machine learning model, the institution must collect:
1. **Historical Allocation Records:** Past course-to-faculty allocations over multiple semesters.
2. **Outcome Labels:** Objective performance indicators such as course completion rates, student evaluation feedback scores, departmental review ratings, and grade distributions.
3. **Faculty Profile Data:** Verified qualification levels, total years of teaching experience, domain specializations, and validated weekly workload caps.
4. **Evaluation Protocol:** Offline cross-validation on real data evaluating Mean Absolute Error (MAE), Root Mean Squared Error (RMSE), and Normalized Discounted Cumulative Gain (NDCG) for candidate ranking.
