import math

import numpy as np
import pytest
from sklearn.naive_bayes import GaussianNB

import course_ml
import fastapi_app as app


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    courses = [
        {
            "course": "Math Reference A",
            "features": [90, 80, 75, 70, 65, 60],
            "description": "",
            "strands": ["STEM"],
        },
        {
            "course": "Math Reference B",
            "features": [95, 85, 80, 75, 70, 65],
            "description": "",
            "strands": ["STEM"],
        },
    ]
    monkeypatch.setattr(app, "_course_training_data", lambda: courses)
    monkeypatch.setattr(app, "_COURSE_MODEL_CACHE", None)
    monkeypatch.setattr(course_ml, "MODEL_PATH", tmp_path / "synthetic_course_model.joblib")
    monkeypatch.setattr(app, "MODEL_PATH", course_ml.MODEL_PATH)
    return courses


def test_real_classifier_is_trained_and_queried(catalog, monkeypatch):
    model = app._build_course_model((0,))
    assert isinstance(model, GaussianNB)
    assert model.n_features_in_ == 1
    assert model.class_count_.tolist() == [1440, 1440]
    queries = []
    original = model.predict_log_proba

    def record_query(query):
        queries.append(query)
        return original(query)

    monkeypatch.setattr(model, "predict_log_proba", record_query)
    results = app.recommend_course("Mathematics - 95", strand="STEM")

    assert queries == [[[95.0]]]
    assert results[0]["course"] == "Math Reference B"
    assert results[0]["model_basis"] == course_ml.MODEL_BASIS
    assert results[0]["match_score"] > results[1]["match_score"]
    assert "synthetic" in results[0]["reason"]


def test_classifier_prediction_not_rule_score_controls_order(catalog, monkeypatch):
    model = app._build_course_model((0,))
    monkeypatch.setattr(model, "predict_log_proba", lambda query: np.log([[0.9, 0.1]]))

    results = app.recommend_course("Mathematics - 95", strand="STEM")

    assert results[0]["course"] == "Math Reference A"
    assert results[0]["match_score"] == 90
    assert results[1]["match_score"] == 10


def test_classifier_order_uses_unrounded_log_support(catalog, monkeypatch):
    model = app._build_course_model((0,))
    monkeypatch.setattr(model, "predict_log_proba", lambda query: np.log([[0.500000001, 0.499999999]]))
    results = app.recommend_course("Mathematics - 95", strand="STEM")

    assert results[0]["course"] == "Math Reference A"
    assert results[0]["match_score"] == results[1]["match_score"] == 50


def test_missing_categories_do_not_affect_predictions(catalog):
    before = app.recommend_course("Mathematics - 94", strand="STEM")
    catalog[0]["features"][1:] = [100, 100, 100, 100, 100]
    catalog[1]["features"][1:] = [0, 0, 0, 0, 0]
    catalog[0]["course"] = "BS in Civil Engineering"
    catalog[1]["course"] = "BS in Mechanical Engineering"
    after = app.recommend_course("Mathematics - 94", strand="STEM")

    assert [item["confidence"] for item in before] == [item["confidence"] for item in after]
    assert [item["match_score"] for item in before] == [item["match_score"] for item in after]
    assert [item["confidence"] for item in after] == [99, 96]
    assert app._COURSE_MODEL_CACHE[1].n_features_in_ == 1


def test_profile_similarity_remains_separate_from_classifier_support(catalog):
    results = app.recommend_course("Mathematics - 90\nScience - 86", strand="STEM")
    by_course = {item["course"]: item for item in results}
    expected_distance = math.sqrt((3 * 0 ** 2 + 3 * 6 ** 2) / 6)

    assert by_course["Math Reference A"]["confidence"] == round(100 - expected_distance, 2)
    assert by_course["Math Reference A"]["match_score"] != by_course["Math Reference A"]["confidence"]


def test_cache_refits_after_profile_changes_and_reuses_unchanged_data(catalog):
    first = app._build_course_model((0,))
    assert app._build_course_model((0,)) is first
    catalog[0]["features"][0] = 99
    second = app._build_course_model((0,))

    assert second is not first
    assert app.recommend_course("Mathematics - 99", strand="STEM")[0]["course"] == "Math Reference A"
    assert app._build_course_model((0, 1)) is not second


def test_all_classes_are_considered_before_cluster_filtering(catalog):
    catalog[:] = [
        {
            "course": f"Outside Cluster {index}",
            "features": [95, 80, 75, 70, 65, 60],
            "description": "",
            "strands": ["BE"],
        }
        for index in range(10)
    ] + [{
        "course": "Eligible Math Course",
        "features": [85, 80, 75, 70, 65, 60],
        "description": "",
        "strands": ["STEM"],
    }]

    results = app.recommend_course("Mathematics - 95", strand="STEM")

    assert [item["course"] for item in results] == ["Eligible Math Course"]
    assert len(app._COURSE_MODEL_CACHE[1].classes_) == 11


def test_model_failures_are_not_replaced_by_rule_based_results(catalog, monkeypatch):
    model = app._build_course_model((0,))

    def fail_query(query):
        raise RuntimeError("Model query failed")

    monkeypatch.setattr(model, "predict_log_proba", fail_query)
    with pytest.raises(RuntimeError, match="Model query failed"):
        app.recommend_course("Mathematics - 95", strand="STEM")


def test_empty_catalog_raises_explicit_error(catalog):
    catalog.clear()

    with pytest.raises(ValueError, match="no active courses"):
        app.recommend_course("Mathematics - 95", strand="STEM")


def test_exact_full_profile_is_top_match(catalog):
    subjects = (
        "Mathematics - 90\nScience - 80\nEnglish - 75\n"
        "Computer Programming - 70\nAccounting - 65\nSocial Studies - 60"
    )
    result = app.recommend_course(subjects, strand="STEM")

    assert result[0]["course"] == "Math Reference A"
    assert result[0]["confidence"] == 100
    assert all(not key.startswith("_") for item in result for key in item)


def test_dataset_is_reproducible_balanced_and_has_no_partition_leakage(catalog):
    rows = course_ml.generate_dataset(catalog)
    assert rows == course_ml.generate_dataset(catalog)
    assert len(rows) == 3000
    scenario_splits = {}
    vector_splits = {}
    for row in rows:
        assert row["source"] == "synthetic_catalog_perturbation"
        assert row["course"] in {item["course"] for item in catalog}
        assert all(0 <= row[feature] <= 100 for feature in course_ml.FEATURES)
        assert scenario_splits.setdefault(row["scenario_id"], row["split"]) == row["split"]
        signature = tuple(row[feature] for feature in course_ml.FEATURES)
        assert vector_splits.setdefault(signature, row["split"]) == row["split"]
    for item in catalog:
        for split, expected in (("train", 1440), ("validation", 30), ("test", 30)):
            assert sum(row["course"] == item["course"] and row["split"] == split for row in rows) == expected


def test_held_out_labels_never_enter_fit(catalog):
    rows = course_ml.generate_dataset(catalog)
    first = course_ml.fit_dataset(rows)
    for row in rows:
        if row["split"] != "train":
            row["course"] = "Held-out poison label"
            row["math"] = 0
    second = course_ml.fit_dataset(rows)

    assert first.classes_.tolist() == second.classes_.tolist()
    np.testing.assert_array_equal(first.theta_, second.theta_)
    np.testing.assert_array_equal(first.var_, second.var_)


def test_extra_training_preserves_original_validation_and_test(catalog, monkeypatch):
    extended = course_ml.generate_dataset(catalog)
    monkeypatch.setattr(course_ml, "EXTRA_TRAINING_SCENARIOS", 0)
    original = course_ml.generate_dataset(catalog)

    assert [row for row in extended if row["split"] != "train"] == [
        row for row in original if row["split"] != "train"
    ]
    assert [row for row in extended if row["scenario_id"] <= course_ml.SCENARIOS] == original
    assert all(row["split"] == "train" for row in extended if row["scenario_id"] > course_ml.SCENARIOS)


def test_artifact_replacement_invalidates_backend_model_cache(catalog, tmp_path):
    course_ml.save_training(catalog, tmp_path)
    first = app._build_course_model((0,))
    course_ml.save_training(catalog, tmp_path)
    second = app._build_course_model((0,))

    assert second is not first
    np.testing.assert_array_equal(first.theta_, second.theta_)


def test_saved_artifact_is_used_and_matches_direct_predictions(catalog, tmp_path, monkeypatch):
    report = course_ml.save_training(catalog, tmp_path)
    assert report["real_world_accuracy"] is None
    assert report["total_samples"] == 3000
    assert report["test_synthetic_only"]["samples"] == 60
    assert report["partition_counts"] == {"train": 2880, "validation": 60, "test": 60}
    assert report["baseline_same_held_out_examples"]["training_samples"] == 480
    assert (tmp_path / "synthetic_course_dataset.csv").is_file()
    assert (tmp_path / "training_report.json").is_file()
    trained = course_ml.train_bundle(catalog)["model"]

    def unexpected_retrain(courses):
        raise AssertionError("Matching artifact must be loaded, not regenerated")

    monkeypatch.setattr(course_ml, "train_bundle", unexpected_retrain)
    loaded = course_ml.model_for_catalog(catalog, range(6))
    partial = course_ml.model_for_catalog(catalog, (0,))
    np.testing.assert_allclose(loaded.predict_proba([[90, 80, 75, 70, 65, 60]]),
                               trained.predict_proba([[90, 80, 75, 70, 65, 60]]))
    assert partial.n_features_in_ == 1


def test_catalog_change_retrains_instead_of_using_stale_saved_model(catalog, tmp_path, caplog):
    course_ml.save_training(catalog, tmp_path)
    catalog.pop()
    model = course_ml.model_for_catalog(catalog, (0,))

    assert model.classes_.tolist() == ["Math Reference A"]
    assert "retraining" in caplog.text


def test_duplicate_profiles_have_identical_distributions(catalog):
    catalog[1]["features"] = catalog[0]["features"][:]
    model = course_ml.fit_dataset(course_ml.generate_dataset(catalog))

    np.testing.assert_array_equal(model.theta_[0], model.theta_[1])
    np.testing.assert_array_equal(model.var_[0], model.var_[1])
    np.testing.assert_allclose(model.predict_proba([[90, 80, 75, 70, 65, 60]]), [[0.5, 0.5]])


@pytest.mark.parametrize("features", [[90], [90, 80, 70, 60, 50, float("nan")], [101] * 6])
def test_invalid_catalog_features_raise(catalog, features):
    catalog[0]["features"] = features
    with pytest.raises(ValueError):
        course_ml.generate_dataset(catalog)


def test_corrupt_saved_artifact_is_not_silently_replaced(catalog, tmp_path):
    course_ml.MODEL_PATH.write_bytes(b"not a trained model")
    with pytest.raises(Exception):
        course_ml.model_for_catalog(catalog, (0,))


def test_synthetic_provenance_survives_sanitizing_and_email(catalog):
    results = app.recommend_course("Mathematics - 95", strand="STEM")
    saved = app._sanitize_recommendations(results)

    assert all(item["model_basis"] == course_ml.MODEL_BASIS for item in saved)
    assert all(course_ml.NOTICE in item["reason"] for item in saved)
    text, html = app._results_email_content({"first_name": "Test"}, saved)
    assert "synthetic model support" in text
    assert "synthetic model support" in html
    assert "not real student outcomes" in text
