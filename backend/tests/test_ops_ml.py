"""Prediction ops on synthetic data with known answers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from prompt_etl.ops import OpError, get_op
from prompt_etl.ops.runner import dry_run_schema


def run(op_id: str, df: pd.DataFrame, cfg: dict):
    spec = get_op(op_id)
    assert spec is not None and spec.run is not None
    return spec.run([df], cfg)


def _linear_df(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    x1, x2 = rng.uniform(0, 100, n), rng.uniform(0, 10, n)
    region = rng.choice(["north", "south"], n)
    y = 3 * x1 - 2 * x2 + np.where(region == "north", 50, 0) + 7
    return pd.DataFrame({"x1": x1, "x2": x2, "region": region, "y": y})


def test_regression_linear_recovers_exact_relationship() -> None:
    df = _linear_df()
    df.loc[:9, "y"] = np.nan  # unlabeled rows get scored
    res = run("predict_regression", df, {"target": "y", "model": "linear"})
    assert res.metrics["r2"] > 0.999
    assert res.metrics["scored_rows"] == 10
    assert set(res.df["split"]) == {"train", "test", "scored"}
    assert res.df["prediction"].notna().all()


@pytest.mark.parametrize("model", ["random_forest", "gradient_boosting"])
def test_regression_tree_models(model: str) -> None:
    res = run("predict_regression", _linear_df(), {"target": "y", "model": model})
    assert res.metrics["r2"] > 0.9
    if model == "random_forest":
        assert res.metrics["importance"][0]["feature"] == "x1"


def test_regression_rejects_text_target() -> None:
    df = _linear_df()
    df["label"] = "a"
    with pytest.raises(OpError, match="not numeric"):
        run("predict_regression", df, {"target": "label"})


@pytest.mark.parametrize("model", ["logistic", "decision_tree", "random_forest", "knn"])
def test_classification(model: str) -> None:
    rng = np.random.default_rng(1)
    n = 400
    a, b = rng.normal(0, 1, n), rng.normal(0, 1, n)
    label = np.where(a + b > 0, "yes", "no")
    res = run("predict_class", pd.DataFrame({"a": a, "b": b, "label": label}),
              {"target": "label", "model": model})
    assert res.metrics["accuracy"] > 0.85
    assert res.metrics["confusion"]["labels"] == ["no", "yes"]
    assert res.df["probability"].between(0, 1).all()


def test_cluster_finds_three_blobs() -> None:
    rng = np.random.default_rng(2)
    centers = [(0, 0), (10, 10), (-10, 10)]
    pts = np.vstack([rng.normal(c, 0.5, (100, 2)) for c in centers])
    res = run("cluster", pd.DataFrame(pts, columns=["x", "y"]), {"k": 3})
    assert sorted(res.metrics["sizes"].values()) == [100, 100, 100]
    assert res.metrics["silhouette"] > 0.8


def test_anomalies_flag_outliers() -> None:
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"v": np.concatenate([rng.normal(50, 1, 300), [500, -400, 900]])})
    res = run("detect_anomalies", df, {"contamination": "0.01"})
    flagged = set(res.df.index[res.df["is_anomaly"]])
    assert {300, 301, 302} <= flagged


def test_forecast_seasonal_monthly() -> None:
    months = pd.date_range("2020-01-01", periods=48, freq="MS")
    season = np.tile([10, 12, 15, 20, 25, 30, 32, 30, 24, 18, 13, 11], 4)
    values = season + np.arange(48) * 0.5
    df = pd.DataFrame({"month": months, "sales": values})
    res = run("forecast", df, {"date_column": "month", "value_column": "sales", "horizon": 12})
    assert res.metrics["frequency"] == "M"
    assert res.metrics["seasonal_periods"] == 12
    fc = res.df[res.df["series"] == "forecast"]
    assert len(fc) == 12
    expected = season[:12] + np.arange(48, 60) * 0.5
    assert np.abs(fc["value"].to_numpy() - expected).max() < 2.0
    assert (fc["lower"] < fc["value"]).all() and (fc["upper"] > fc["value"]).all()


def test_forecast_linear_trend_daily_from_text_dates() -> None:
    days = pd.date_range("2024-01-01", periods=30, freq="D").strftime("%Y-%m-%d")
    df = pd.DataFrame({"d": days, "v": np.arange(30) * 2.0 + 5})
    res = run("forecast", df, {"date_column": "d", "value_column": "v", "horizon": 5,
                               "method": "linear_trend"})
    fc = res.df[res.df["series"] == "forecast"]["value"].to_numpy()
    assert np.allclose(fc, [65, 67, 69, 71, 73])


def test_ml_output_schemas_match_runs() -> None:
    df = _linear_df(60)
    schema = [("x1", "DOUBLE"), ("x2", "DOUBLE"), ("region", "VARCHAR"), ("y", "DOUBLE")]
    for op_id, cfg in [
        ("predict_regression", {"target": "y"}),
        ("predict_class", {"target": "region"}),
        ("cluster", {"k": 2}),
        ("detect_anomalies", {}),
    ]:
        predicted = [n for n, _ in dry_run_schema(get_op(op_id), cfg, [schema])]
        assert predicted == list(run(op_id, df, cfg).df.columns), op_id
