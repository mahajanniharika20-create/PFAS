"""Basic tests for feature validation and hard-cap helper."""
import unittest
from ml.features import build_feature_frame
from ml.constraints import passes_basic_constraints

VALID = {
    "faculty_domain": "AI",
    "subject_domain": "AI",
    "primary_preference": "AI",
    "secondary_preference": "Data Science",
    "experience_years": 8,
    "related_subject_experience_years": 5,
    "current_weekly_hours": 6,
    "weekly_hour_cap": 14,
}

class FeatureTests(unittest.TestCase):
    def test_valid_features(self):
        frame = build_feature_frame(VALID)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["subject_domain"], "AI")

    def test_rejects_missing_feature(self):
        bad = dict(VALID)
        del bad["faculty_domain"]
        with self.assertRaises(ValueError):
            build_feature_frame(bad)

    def test_hour_cap(self):
        ok, _ = passes_basic_constraints(VALID, required_hours=2)
        self.assertTrue(ok)
        ok, _ = passes_basic_constraints(VALID, required_hours=9)
        self.assertFalse(ok)

if __name__ == "__main__":
    unittest.main()
