"""
GRUS — Risk model training

Five binary XGBoost models, one per label, trained on the same features.

Not one multi-output model: the labels have very different base rates
(1.9% to 36%), and a shared model tunes to the common ones. Separate
models each get their own scale_pos_weight and their own threshold.

Every model is checked for three things before it ships:

  discrimination  AUC and average precision. AUC alone flatters a model
                  on imbalanced data; AP is the honest number when
                  positives are 5%.
  calibration     does a predicted 0.7 mean it happens 70% of the time?
                  An uncalibrated score is a number a doctor cannot use.
  fairness        does performance hold across age and sex? A model that
                  works on 40-year-olds and fails on 80-year-olds is not
                  ready, whatever its overall AUC.

Models failing any gate are reported, not silently shipped.
"""

import os
import json
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (roc_auc_score, average_precision_score,
                             brier_score_loss, precision_recall_curve)
from sklearn.calibration import calibration_curve
from sklearn.isotonic import IsotonicRegression
from grus_config import Paths
MODEL_DIR = str(Paths.MODEL_DIR)


ALL_LABEL_COLS = [
    "needs_transfusion_12h", "bleeding_progression_6h", "aki_risk_24h",
    "neuro_decline_6h", "sepsis_progression_12h",
    "electrolyte_crisis_12h", "respiratory_decline_6h",
]

# Columns that identify a row rather than describe it. Training on
# stay_id would let the model memorise patients.
ID_COLS = ["stay_id", "hadm_id", "subject_id", "hour_bin", "end_h",
           "died_in_hospital"]

# Gates a model must pass to be used. Deliberately modest — the point is
# to catch models that are useless, not to demand excellence.
MIN_AUC = 0.65
MAX_CALIBRATION_ERROR = 0.10
MAX_SUBGROUP_AUC_GAP = 0.10


MAX_ALERT_RATE = 0.35


MAX_SINGLE_FEATURE_GAIN = 0.40


# ---------------------------------------------------------------
# Threshold policy
#
# The F1 optimum balances precision and recall equally. Clinically they
# are rarely equal — the cost of a false alarm and the cost of a miss
# differ by label, sometimes by a lot.
#
#   "recall"    catch as much as possible, tolerate false alarms.
#               Use when preparing for something is cheap and being
#               unprepared is dangerous. Cross-matching blood that is
#               never transfused costs a technician's time; not having
#               blood ready for someone haemorrhaging costs more.
#
#   "balanced"  the F1 point. Use when the two costs are comparable.
#
#   "precision" fire rarely, be right when you do. Use when acting on
#               the alert is invasive, expensive, or itself risky.
#
# min_recall / min_precision set the floor the threshold search must
# meet before optimising anything else.
# ---------------------------------------------------------------
THRESHOLD_POLICY = {
    "needs_transfusion_12h": {
        "mode": "recall", "min_recall": 0.50,
        "why": "Preparing blood that is not used is cheap. Not having it "
               "ready for a bleeding patient is not. Floor set at 50%: "
               "the curve knees sharply after that — 72% recall costs "
               "precision 0.21, four alerts wrong in five.",
    },
    "aki_risk_24h": {
        "mode": "balanced",
        "why": "Acting means holding contrast and reviewing nephrotoxic "
               "drugs — worth doing on suspicion, but not on every "
               "patient.",
    },
    "electrolyte_crisis_12h": {
        "mode": "recall", "min_recall": 0.50,
        "why": "A potassium heading above 6 causes arrhythmias. Checking "
               "an extra sample is trivial beside missing one. Floor at "
               "50%: precision falls 0.47 to 0.26 between 51% and 61% "
               "recall, so the extra ten points are not affordable.",
    },
    "sepsis_progression_12h": {"mode": "balanced"},
    "respiratory_decline_6h": {"mode": "balanced"},
    "bleeding_progression_6h": {"mode": "recall", "min_recall": 0.50},
    "neuro_decline_6h": {"mode": "recall", "min_recall": 0.50},
}

# Minimum precision for a shipped model. Below this a clinician is being
# asked to act on a coin flip, and alert fatigue sets in fast.
MIN_PRECISION = 0.35


def _wrap(text, width):
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def load():
    train = pd.read_parquet(f"{MODEL_DIR}/train.parquet")
    val = pd.read_parquet(f"{MODEL_DIR}/val.parquet")
    test = pd.read_parquet(f"{MODEL_DIR}/test.parquet")

    with open(f"{MODEL_DIR}/labels.txt") as f:
        labels = [l.strip() for l in f if l.strip()]

    feature_cols = [c for c in train.columns
                    if c not in ID_COLS
                    and c not in ALL_LABEL_COLS
                    and train[c].dtype.kind in "ifb"]

    leaked = [c for c in train.columns if c in ALL_LABEL_COLS
              and c not in labels]
    print(f"train {len(train):,}  val {len(val):,}  test {len(test):,}")
    print(f"{len(feature_cols)} features, {len(labels)} labels")
    if leaked:
        print(f"excluded {len(leaked)} dropped labels from features: {leaked}")
    return train, val, test, feature_cols, labels


def train_one(train, val, feature_cols, label):
    """
    One label, one model.

    scale_pos_weight compensates for imbalance. Without it a model
    predicting 'no' every time scores 95% accuracy on a 5% label and is
    completely useless.
    """
    Xtr, ytr = train[feature_cols], train[label]
    Xva, yva = val[feature_cols], val[label]

    pos = ytr.sum()
    neg = len(ytr) - pos
    spw = neg / pos if pos else 1.0

    model = xgb.XGBClassifier(
        n_estimators=400,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=20,        # resist fitting to single patients
        reg_lambda=2.0,
        scale_pos_weight=spw,
        eval_metric="aucpr",
        early_stopping_rounds=30,
        n_jobs=-1,
        tree_method="hist",
        random_state=42,
    )
    model.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
    return model


def calibrate(model, val, feature_cols, label):
    """
    Isotonic regression on the validation set.

    scale_pos_weight makes raw XGBoost output badly calibrated — it is
    optimised for ranking, not for probability. A doctor reading '0.7'
    should be seeing something that happens roughly 70% of the time.
    """
    p = model.predict_proba(val[feature_cols])[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(p, val[label])
    return iso


def choose_threshold(y, p, label):
    """
    Pick the operating point from the label's clinical policy.

    A default of 0.5 is arbitrary on imbalanced data, and the F1 optimum
    assumes a false alarm and a miss cost the same. For most of these
    labels they do not.

    Returns (threshold, description, curve) — the curve lets the caller
    show what the alternatives would have been.
    """
    prec, rec, thr = precision_recall_curve(y, p)
    if len(thr) == 0:
        return 0.5, "default", []

    prec, rec = prec[:-1], rec[:-1]          # align with thr
    f1 = 2 * prec * rec / np.clip(prec + rec, 1e-9, None)

    policy = THRESHOLD_POLICY.get(label, {"mode": "balanced"})
    mode = policy.get("mode", "balanced")

    if mode == "recall":
        floor = policy.get("min_recall", 0.70)
        ok = rec >= floor
        if ok.any():
            # cheapest way to reach the recall floor: of the thresholds
            # that meet it, take the most precise
            idx = int(np.argmax(np.where(ok, prec, -1)))
            desc = f"recall floor {floor:.0%}"
        else:
            idx = int(np.nanargmax(f1))
            desc = f"recall floor {floor:.0%} unreachable, fell back to F1"

    elif mode == "precision":
        floor = policy.get("min_precision", 0.70)
        ok = prec >= floor
        if ok.any():
            idx = int(np.argmax(np.where(ok, rec, -1)))
            desc = f"precision floor {floor:.0%}"
        else:
            idx = int(np.nanargmax(f1))
            desc = f"precision floor {floor:.0%} unreachable, fell back to F1"

    else:
        idx = int(np.nanargmax(f1))
        desc = "F1 optimum"

   
    curve = []
    for target in [0.50, 0.60, 0.70, 0.80, 0.90]:
        ok = rec >= target
        if ok.any():
            j = int(np.argmax(np.where(ok, prec, -1)))
            curve.append({
                "threshold": round(float(thr[j]), 4),
                "recall": round(float(rec[j]), 3),
                "precision": round(float(prec[j]), 3),
            })

    return float(thr[idx]), desc, curve


def evaluate(model, iso, test, feature_cols, label, threshold):
    raw = model.predict_proba(test[feature_cols])[:, 1]
    p = iso.predict(raw)
    y = test[label].values

    out = {
        "label": label,
        "auc": float(roc_auc_score(y, p)),
        "average_precision": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "positive_rate": float(y.mean()),
        "threshold": threshold,
    }

    # Calibration: bin predictions, compare mean prediction to actual rate
    try:
        frac_pos, mean_pred = calibration_curve(y, p, n_bins=10,
                                                strategy="quantile")
        out["calibration_error"] = float(np.mean(np.abs(frac_pos - mean_pred)))
    except Exception:
        out["calibration_error"] = None

    # At the chosen threshold
    pred = (p >= threshold).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    out["precision"] = tp / (tp + fp) if tp + fp else 0.0
    out["recall"] = tp / (tp + fn) if tp + fn else 0.0
    out["alert_rate"] = float(pred.mean())

    return out, p


def fairness(test, p, y, label):
    """
    Subgroup performance.

    A model with an overall AUC of 0.80 that scores 0.85 on younger
    patients and 0.62 on the elderly is not one model, it is two, and the
    second one is failing the people most likely to be in an ICU.
    """
    groups = {
        "age_under_65": test["anchor_age"] < 65,
        "age_65_plus": test["anchor_age"] >= 65,
        "male": test["is_male"] == 1,
        "female": test["is_male"] == 0,
    }

    out, aucs = {}, []
    for name, mask in groups.items():
        m = mask.values
        if m.sum() < 200 or y[m].sum() < 20 or y[m].sum() == m.sum():
            out[name] = None
            continue
        a = float(roc_auc_score(y[m], p[m]))
        out[name] = {"auc": round(a, 3), "n": int(m.sum()),
                     "positive_rate": round(float(y[m].mean()), 3)}
        aucs.append(a)

    out["max_gap"] = round(max(aucs) - min(aucs), 3) if len(aucs) > 1 else 0.0
    return out


def top_features(model, feature_cols, k=10):
    imp = model.feature_importances_
    order = np.argsort(imp)[::-1][:k]
    return [{"feature": feature_cols[i], "gain": round(float(imp[i]), 4)}
            for i in order]


def gates(metrics, fair, feats):
    """What must hold before a model is used."""
    failures = []
    if metrics["auc"] < MIN_AUC:
        failures.append(f"AUC {metrics['auc']:.3f} below {MIN_AUC}")

    ce = metrics.get("calibration_error")
    if ce is not None and ce > MAX_CALIBRATION_ERROR:
        failures.append(f"calibration error {ce:.3f} above {MAX_CALIBRATION_ERROR}")

    if fair["max_gap"] > MAX_SUBGROUP_AUC_GAP:
        failures.append(f"subgroup AUC gap {fair['max_gap']:.3f} "
                        f"above {MAX_SUBGROUP_AUC_GAP}")

    
    if metrics["alert_rate"] > MAX_ALERT_RATE:
        failures.append(f"alerts on {metrics['alert_rate']:.0%} of hours, "
                        f"above {MAX_ALERT_RATE:.0%}")

    
    if metrics["precision"] < MIN_PRECISION:
        failures.append(f"precision {metrics['precision']:.2f} below "
                        f"{MIN_PRECISION} — the recall floor is too high "
                        f"for this model")

    
    if feats and feats[0]["gain"] > MAX_SINGLE_FEATURE_GAIN:
        failures.append(f"'{feats[0]['feature']}' carries "
                        f"{feats[0]['gain']:.0%} of the model — likely "
                        f"restating a value rather than predicting")

    return failures


def run():
    train, val, test, feature_cols, labels = load()
    os.makedirs(f"{MODEL_DIR}/models", exist_ok=True)

    report = {}
    passed, failed = [], []

    for label in labels:
        print(f"\n{'=' * 62}")
        print(label)
        print("=" * 62)

        model = train_one(train, val, feature_cols, label)
        iso = calibrate(model, val, feature_cols, label)

        p_val = iso.predict(model.predict_proba(val[feature_cols])[:, 1])
        thr, thr_desc, curve = choose_threshold(val[label].values, p_val, label)

        metrics, p_test = evaluate(model, iso, test, feature_cols, label, thr)
        metrics["threshold_policy"] = thr_desc
        metrics["operating_curve"] = curve

        fair = fairness(test, p_test, test[label].values, label)
        feats = top_features(model, feature_cols)
        fails = gates(metrics, fair, feats)

        print(f"  AUC              {metrics['auc']:.3f}")
        print(f"  avg precision    {metrics['average_precision']:.3f}  "
              f"(base rate {metrics['positive_rate']:.3f})")
        ce = metrics.get("calibration_error")
        print(f"  calibration err  {ce:.3f}" if ce is not None
              else "  calibration err  n/a")

        policy = THRESHOLD_POLICY.get(label, {})
        print(f"  threshold        {thr:.3f}  ({thr_desc})")
        if policy.get("why"):
            for line in _wrap(policy["why"], 56):
                print(f"                   {line}")
        print(f"    at this point  precision {metrics['precision']:.2f}, "
              f"recall {metrics['recall']:.2f}, "
              f"alerts on {metrics['alert_rate']:.1%} of hours")

        if curve:
            print("    other operating points:")
            print(f"      {'recall':>8} {'precision':>10} {'cutoff':>8}")
            for pt in curve:
                print(f"      {pt['recall']:>8.2f} {pt['precision']:>10.2f} "
                      f"{pt['threshold']:>8.3f}")

        print("  subgroups:")
        for g in ["age_under_65", "age_65_plus", "male", "female"]:
            v = fair.get(g)
            print(f"    {g:16} " + (f"AUC {v['auc']:.3f}  n={v['n']:,}"
                                    if v else "insufficient data"))
        print(f"    max gap          {fair['max_gap']:.3f}")

        print("  top features:")
        for f in feats[:5]:
            print(f"    {f['feature']:24} {f['gain']:.4f}")

        if fails:
            print(f"  FAILED GATES: {'; '.join(fails)}")
            failed.append(label)
        else:
            print("  passed all gates")
            passed.append(label)

        model.save_model(f"{MODEL_DIR}/models/{label}.json")
        np.save(f"{MODEL_DIR}/models/{label}_iso_x.npy", iso.X_thresholds_)
        np.save(f"{MODEL_DIR}/models/{label}_iso_y.npy", iso.y_thresholds_)

        report[label] = {"metrics": metrics, "fairness": fair,
                         "top_features": feats, "gate_failures": fails}

    with open(f"{MODEL_DIR}/models/report.json", "w") as f:
        json.dump({"features": feature_cols, "labels": report,
                   "passed": passed, "failed": failed}, f, indent=2)
    with open(f"{MODEL_DIR}/models/features.json", "w") as f:
        json.dump(feature_cols, f)

    # Only models that passed every gate are served. The rest stay on
    # disk for the record but are not offered to a clinician.
    with open(f"{MODEL_DIR}/models/shipped.txt", "w") as f:
        f.write("\n".join(passed))


    with open(f"{MODEL_DIR}/models/thresholds.json", "w") as f:
        json.dump({lbl: {
            "threshold": report[lbl]["metrics"]["threshold"],
            "policy": report[lbl]["metrics"].get("threshold_policy"),
            "precision": round(report[lbl]["metrics"]["precision"], 3),
            "recall": round(report[lbl]["metrics"]["recall"], 3),
            "alert_rate": round(report[lbl]["metrics"]["alert_rate"], 4),
            "operating_curve": report[lbl]["metrics"].get("operating_curve"),
        } for lbl in passed}, f, indent=2)

    print(f"\n{'=' * 62}")
    print(f"passed {len(passed)}: {passed}")
    if failed:
        print(f"failed {len(failed)}: {failed}")
        for lbl in failed:
            for reason in report[lbl]["gate_failures"]:
                print(f"    {lbl}: {reason}")
        print("  reported, not shipped — a model that fails calibration or")
        print("  fires constantly produces numbers a clinician cannot act on")
    print(f"\nsaved to {MODEL_DIR}/models/")
    print(f"shipped labels written to {MODEL_DIR}/models/shipped.txt")


if __name__ == "__main__":
    run()
