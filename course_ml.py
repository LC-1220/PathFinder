"""Synthetic course-profile training. No real student outcomes are represented."""

import argparse
import csv
import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
import re

import joblib
import numpy as np
import sklearn
from sklearn.metrics import accuracy_score, f1_score
from sklearn.naive_bayes import GaussianNB


FEATURES = ("math", "science", "english", "technology", "business", "social")
SEED = 20261105
SCENARIOS = 300
EXTRA_TRAINING_SCENARIOS = 1200
TRAINING_REVISION = 3
MODEL_BASIS = "synthetic_gaussian_nb_v1"
NOTICE = (
    "Experimental model trained on synthetic course-profile examples, not real "
    "student outcomes. Model support is not an admission or success probability."
)
ARTIFACT_DIR = Path(__file__).resolve().parent / "ml_artifacts"
MODEL_PATH = ARTIFACT_DIR / "synthetic_course_model.joblib"


def catalog_fingerprint(catalog):
    payload = [(item["course"], list(item["features"])) for item in catalog]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()


def generate_dataset(catalog, seed=SEED):
    if not catalog:
        raise ValueError("The recommendation catalog has no active courses.")
    names = [item["course"] for item in catalog]
    if len(set(names)) != len(names):
        raise ValueError("Synthetic training requires unique course names.")
    centers = np.asarray([item["features"] for item in catalog], dtype=float)
    if centers.shape != (len(catalog), len(FEATURES)):
        raise ValueError("Every course must have six reference grades.")
    if not np.isfinite(centers).all() or np.any((centers < 0) | (centers > 100)):
        raise ValueError("Course reference grades must be finite values from 0 to 100.")

    rng = np.random.default_rng(seed)
    # Share scenarios across courses so identical profiles remain indistinguishable.
    noise = rng.normal(0, 4, (SCENARIOS, len(FEATURES)))
    noise += rng.normal(0, 2, (SCENARIOS, 1))
    splits = np.array(["train"] * 240 + ["validation"] * 30 + ["test"] * 30)
    rng.shuffle(splits)
    rows = []
    for course_index, item in enumerate(catalog):
        for scenario, delta in enumerate(noise):
            grades = np.round(np.clip(centers[course_index] + delta, 0, 100), 2)
            rows.append({
                "sample_id": f"synthetic-{course_index + 1:03d}-{scenario + 1:03d}",
                "scenario_id": scenario + 1,
                "split": str(splits[scenario]),
                "source": "synthetic_catalog_perturbation",
                "course": item["course"],
                **dict(zip(FEATURES, grades.tolist())),
            })
    # Identical feature rows must never cross split boundaries, even after clipping.
    assigned = {}
    parents = list(range(SCENARIOS + 1))

    def root(index):
        while parents[index] != index:
            index = parents[index]
        return index

    for row in rows:
        signature = tuple(row[feature] for feature in FEATURES)
        previous = assigned.setdefault(signature, row["scenario_id"])
        left, right = root(previous), root(row["scenario_id"])
        parents[max(left, right)] = min(left, right)
    for row in rows:
        row["split"] = str(splits[root(row["scenario_id"]) - 1])
    # Preserve the original held-out scenarios exactly; only add training rows.
    extra_rng = np.random.default_rng(seed + 1)
    held_out_signatures = {
        tuple(row[feature] for feature in FEATURES)
        for row in rows if row["split"] != "train"
    }
    for scenario in range(SCENARIOS, SCENARIOS + EXTRA_TRAINING_SCENARIOS):
        for attempt in range(100):
            delta = extra_rng.normal(0, 4, len(FEATURES)) + extra_rng.normal(0, 2)
            grades = np.round(np.clip(centers + delta, 0, 100), 2)
            candidate_signatures = {tuple(values) for values in grades}
            if not candidate_signatures.intersection(held_out_signatures):
                break
        else:
            raise ValueError("Could not generate independent additional training scenarios.")
        for course_index, item in enumerate(catalog):
            rows.append({
                "sample_id": f"synthetic-{course_index + 1:03d}-{scenario + 1:03d}",
                "scenario_id": scenario + 1,
                "split": "train",
                "source": "synthetic_catalog_perturbation",
                "course": item["course"],
                **dict(zip(FEATURES, grades[course_index].tolist())),
            })
    return rows


def fit_dataset(rows, observed_indices=None):
    indices = tuple(range(len(FEATURES))) if observed_indices is None else tuple(observed_indices)
    if not indices or any(index not in range(len(FEATURES)) for index in indices):
        raise ValueError("At least one valid observed subject dimension is required.")
    train = [row for row in rows if row["split"] == "train"]
    if not train:
        raise ValueError("The synthetic dataset has no training examples.")
    x = np.asarray([[row[FEATURES[index]] for index in indices] for row in train])
    y = np.asarray([row["course"] for row in train])
    return GaussianNB().fit(x, y)


def _metrics(model, rows, split, indices=tuple(range(len(FEATURES)))):
    selected = [row for row in rows if row["split"] == split]
    x = np.asarray([[row[FEATURES[index]] for index in indices] for row in selected])
    actual = np.asarray([row["course"] for row in selected])
    log_support = model.predict_log_proba(x)
    order = np.argsort(-log_support, axis=1, kind="stable")
    predicted = model.classes_[order[:, 0]]
    top_three = model.classes_[order[:, :min(3, len(model.classes_))]]
    return {
        "samples": len(selected),
        "top_1_agreement": float(accuracy_score(actual, predicted)),
        "top_3_agreement": float(np.mean(np.any(top_three == actual[:, None], axis=1))),
        "macro_f1": float(f1_score(actual, predicted, average="macro", zero_division=0)),
    }


def train_bundle(catalog):
    rows = generate_dataset(catalog)
    return {
        "model_basis": MODEL_BASIS,
        "training_revision": TRAINING_REVISION,
        "catalog_fingerprint": catalog_fingerprint(catalog),
        "catalog": [{"course": item["course"], "features": list(item["features"])} for item in catalog],
        "rows": rows,
        "model": fit_dataset(rows),
    }


@lru_cache(maxsize=1)
def _load_bundle(path, modified_ns):
    # Only load the application's own locally generated artifact, never uploads.
    return joblib.load(path)


def model_for_catalog(catalog, observed_indices):
    fingerprint = catalog_fingerprint(catalog)
    if MODEL_PATH.exists():
        bundle = _load_bundle(str(MODEL_PATH), MODEL_PATH.stat().st_mtime_ns)
        if bundle["model_basis"] != MODEL_BASIS:
            raise ValueError("The trained recommendation artifact has an unsupported version.")
        if (
            bundle["catalog_fingerprint"] != fingerprint
            or bundle.get("training_revision") != TRAINING_REVISION
        ):
            logging.getLogger(__name__).warning(
                "Catalog or training revision differs from the saved synthetic model; "
                "retraining for the current active catalog."
            )
            bundle = train_bundle(catalog)
    else:
        logging.getLogger(__name__).warning(
            "No saved synthetic model found; training from current active course profiles."
        )
        bundle = train_bundle(catalog)
    if tuple(observed_indices) == tuple(range(len(FEATURES))):
        return bundle["model"]
    # Retrain the same training partition on observed columns only, without imputation.
    return fit_dataset(bundle["rows"], observed_indices)


def save_training(catalog, output_dir=ARTIFACT_DIR):
    bundle = train_bundle(catalog)
    rows = bundle["rows"]
    model = bundle["model"]
    baseline_rows = [row for row in rows if row["scenario_id"] <= SCENARIOS]
    baseline_model = fit_dataset(baseline_rows)
    duplicate_profiles = {}
    for item in catalog:
        duplicate_profiles.setdefault(tuple(item["features"]), []).append(item["course"])
    report = {
        "model_basis": MODEL_BASIS,
        "training_revision": TRAINING_REVISION,
        "notice": NOTICE,
        "real_world_accuracy": None,
        "seed": SEED,
        "algorithm": "GaussianNB",
        "scikit_learn_version": sklearn.__version__,
        "features": list(FEATURES),
        "catalog_fingerprint": bundle["catalog_fingerprint"],
        "courses": len(catalog),
        "total_samples": len(rows),
        "partition_counts": {
            split: sum(row["split"] == split for row in rows)
            for split in ("train", "validation", "test")
        },
        "generation": {
            "label": "Originating catalog course; not adviser-verified suitability",
            "category_noise_stddev": 4,
            "shared_grade_offset_stddev": 2,
            "grade_bounds": [0, 100],
            "scenarios_per_course": SCENARIOS + EXTRA_TRAINING_SCENARIOS,
            "extra_training_scenarios_per_course": EXTRA_TRAINING_SCENARIOS,
            "partition": "Original 240 train / 30 validation / 30 test scenarios per course; 1200 extra train only",
            "selection": "Fixed model and generation settings; no tuning on test results",
            "extra_training_seed": SEED + 1,
        },
        "identical_course_profiles": [names for names in duplicate_profiles.values() if len(names) > 1],
        "validation_synthetic_only": _metrics(model, rows, "validation"),
        "test_synthetic_only": _metrics(model, rows, "test"),
        "test_partial_core_only_synthetic": _metrics(
            fit_dataset(rows, (0, 1, 2)), rows, "test", (0, 1, 2)
        ),
        "baseline_same_held_out_examples": {
            "training_samples": sum(row["split"] == "train" for row in baseline_rows),
            "validation_synthetic_only": _metrics(baseline_model, baseline_rows, "validation"),
            "test_synthetic_only": _metrics(baseline_model, baseline_rows, "test"),
        },
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "synthetic_course_dataset.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "catalog_snapshot.json").write_text(
        json.dumps(bundle["catalog"], indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "training_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    joblib.dump(bundle, output_dir / MODEL_PATH.name, compress=3)
    return report


def _schema_catalog(path):
    text = Path(path).read_text(encoding="utf-8")
    matches = re.findall(r"^\('((?:[^']|'')+)', '(\[[^']+\])',", text, re.MULTILINE)
    if not matches:
        raise ValueError("No course reference profiles were found in the schema.")
    return [{"course": name.replace("''", "'"), "features": json.loads(values)} for name, values in matches]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("schema", "database"), default="schema")
    parser.add_argument("--output-dir", type=Path, default=ARTIFACT_DIR)
    args = parser.parse_args()
    if args.source == "database":
        from fastapi_app import _course_training_data
        catalog = _course_training_data()
    else:
        catalog = _schema_catalog(Path(__file__).resolve().parent / "supabase_schema.sql")
    report = save_training(catalog, args.output_dir)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
