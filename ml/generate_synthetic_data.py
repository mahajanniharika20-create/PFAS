"""Generate synthetic training data for demonstration only."""
import numpy as np
import pandas as pd
from ml.config import DATASET_PATH, DATA_DIR, RANDOM_STATE

DOMAINS = ["AI", "Data Science", "Web Development", "Networks", "Database", "Security"]

def generate_dataset(rows: int = 6000) -> pd.DataFrame:
    rng = np.random.default_rng(RANDOM_STATE)
    records = []
    for _ in range(rows):
        subject_domain = str(rng.choice(DOMAINS))
        faculty_domain = str(rng.choice(DOMAINS))
        primary = str(rng.choice(DOMAINS))
        secondary = str(rng.choice(DOMAINS))
        experience = int(rng.integers(0, 26))
        related_exp = float(round(rng.uniform(0, experience), 1)) if experience else 0.0
        weekly_cap = int(rng.choice([10, 12, 14, 16, 18, 20]))
        current_hours = int(rng.integers(0, weekly_cap + 1))

        domain_match = int(faculty_domain == subject_domain)
        primary_match = int(primary == subject_domain)
        secondary_match = int(secondary == subject_domain)
        remaining_capacity = max(0, weekly_cap - current_hours) / max(weekly_cap, 1)

        score = (
            35 * domain_match
            + 20 * primary_match
            + 10 * secondary_match
            + min(experience, 15) * 1.2
            + min(related_exp, 10) * 1.5
            + 10 * remaining_capacity
            + rng.normal(0, 4)
        )
        score = float(np.clip(score, 0, 100))
        records.append({
            "faculty_domain": faculty_domain,
            "subject_domain": subject_domain,
            "primary_preference": primary,
            "secondary_preference": secondary,
            "experience_years": experience,
            "related_subject_experience_years": related_exp,
            "current_weekly_hours": current_hours,
            "weekly_hour_cap": weekly_cap,
            "suitability_score": round(score, 2),
        })
    return pd.DataFrame(records)

if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = generate_dataset()
    df.to_csv(DATASET_PATH, index=False)
    print(f"Saved {len(df)} synthetic rows to: {DATASET_PATH}")
    print("Reminder: scores are illustrative synthetic labels, not real institutional outcomes.")
