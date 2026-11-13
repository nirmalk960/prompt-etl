"""Prediction operations (scikit-learn, statsmodels). Heavy imports are lazy."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from .base import OpError, OpSpec, Param, PyResult, Schema, opts
from .registry import register
from .sqlsafe import to_int, to_number

if TYPE_CHECKING:
    import pandas as pd

MAX_TRAIN_ROWS = 1_000_000
MAX_CATEGORIES = 50
MAX_CLASSES = 50


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _col(df: "pd.DataFrame", name: Any, what: str = "column") -> str:
    if not isinstance(name, str) or not name.strip():
        raise OpError(f"{what} is required")
    if name in df.columns:
        return name
    lowered = {str(c).lower(): c for c in df.columns}
    if name.strip().lower() in lowered:
        return lowered[name.strip().lower()]
    raise OpError(f"unknown {what} '{name}'")


def _as_datetime(s: "pd.Series") -> "pd.Series | None":
    import pandas as pd

    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    if s.dtype == object and pd.api.types.infer_dtype(s, skipna=True) in {"date", "datetime", "datetime64"}:
        return pd.to_datetime(s, errors="coerce")
    return None


def _features(df: "pd.DataFrame", cfg: dict[str, Any], exclude: set[str]) -> list[str]:
    import pandas as pd

    chosen = cfg.get("features") or []
    if chosen:
        cols = [_col(df, f, "feature") for f in chosen]
        bad = [c for c in cols if c in exclude]
        if bad:
            raise OpError(f"'{bad[0]}' can't be both the target and a feature")
        return cols
    auto = []
    for c in df.columns:
        if c in exclude:
            continue
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s) or _as_datetime(s) is not None:
            auto.append(c)
        elif s.nunique(dropna=True) <= MAX_CATEGORIES:
            auto.append(c)
    if not auto:
        raise OpError("no usable feature columns; choose features explicitly")
    return auto


def _encode(df: "pd.DataFrame", features: list[str]) -> "pd.DataFrame":
    """Numeric matrix: numbers as-is, dates as days, text one-hot (top categories)."""
    import pandas as pd

    parts: list[pd.DataFrame] = []
    for c in features:
        s = df[c]
        dt = _as_datetime(s)
        if pd.api.types.is_bool_dtype(s):
            parts.append(s.astype(float).to_frame(str(c)))
        elif pd.api.types.is_numeric_dtype(s):
            parts.append(pd.to_numeric(s, errors="coerce").astype(float).to_frame(str(c)))
        elif dt is not None:
            days = (dt - pd.Timestamp("1970-01-01")).dt.total_seconds() / 86400.0
            parts.append(days.to_frame(str(c)))
        else:
            vals = s.astype("string").fillna("(missing)")
            top = vals.value_counts().index[:MAX_CATEGORIES]
            vals = vals.where(vals.isin(top), "(other)")
            parts.append(pd.get_dummies(vals, prefix=str(c), dtype=float))
    x = pd.concat(parts, axis=1)
    x = x.fillna(x.median(numeric_only=True)).fillna(0.0)
    if x.shape[1] == 0:
        raise OpError("no usable feature columns")
    return x


def _new_col(df: "pd.DataFrame", name: Any, default: str) -> str:
    out = (name or default).strip() if isinstance(name, str) else default
    if out in df.columns:
        raise OpError(f"column '{out}' already exists; choose another output name")
    return out


def _split(index: "pd.Index", cfg: dict[str, Any], stratify: "pd.Series | None" = None):
    import numpy as np
    from sklearn.model_selection import train_test_split

    seed = to_int(cfg.get("seed", 42), "seed", minimum=0)
    test_size = float(to_number(cfg.get("test_size", 0.2), "test share"))
    if not 0 < test_size < 0.9:
        raise OpError("test share must be between 0 and 0.9")
    notice = None
    if len(index) > MAX_TRAIN_ROWS:
        rng = np.random.default_rng(seed)
        index = index[np.sort(rng.choice(len(index), MAX_TRAIN_ROWS, replace=False))]
        notice = f"trained on a {MAX_TRAIN_ROWS:,}-row sample"
        if stratify is not None:
            stratify = stratify.loc[index]
    if stratify is not None and stratify.value_counts().min() < 2:
        stratify = None
    train, test = train_test_split(
        index, test_size=test_size, random_state=seed,
        stratify=stratify.loc[index] if stratify is not None else None,
    )
    return train, test, notice


def _importances(model: Any, columns: list[str]) -> list[dict[str, Any]]:
    import numpy as np

    est = model.steps[-1][1] if hasattr(model, "steps") else model
    values = getattr(est, "feature_importances_", None)
    if values is None and hasattr(est, "coef_"):
        coef = np.atleast_2d(est.coef_)
        values = np.abs(coef).mean(axis=0)
    if values is None:
        return []
    total = float(np.sum(values)) or 1.0
    ranked = sorted(zip(columns, values), key=lambda kv: -kv[1])[:10]
    return [{"feature": k, "importance": round(float(v) / total, 4)} for k, v in ranked]


def _split_column(n_index: "pd.Index", train: Any, test: Any) -> "pd.Series":
    import pandas as pd

    s = pd.Series("scored", index=n_index, dtype="object")
    s.loc[train] = "train"
    s.loc[test] = "test"
    return s


_COMMON = (
    Param("test_size", "Test share", "number", default=0.2, help="Share of labelled rows held out for scoring"),
    Param("seed", "Random seed", "integer", default=42),
)


# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------


def _regression(dfs: list["pd.DataFrame"], cfg: dict[str, Any]) -> PyResult:
    import numpy as np
    import pandas as pd
    from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    df = dfs[0].copy()
    target = _col(df, cfg.get("target"), "target")
    y = pd.to_numeric(df[target], errors="coerce")
    if y.notna().sum() == 0 and df[target].notna().any():
        raise OpError(f"'{target}' is not numeric; use classification for categories")
    features = _features(df, cfg, {target})
    x = _encode(df, features)
    labeled = df.index[y.notna()]
    if len(labeled) < 10:
        raise OpError(f"need at least 10 rows with a value in '{target}'")
    train, test, notice = _split(labeled, cfg)
    kind = cfg.get("model") or "random_forest"
    seed = to_int(cfg.get("seed", 42), "seed")
    if kind == "linear":
        model: Any = LinearRegression()
    elif kind == "random_forest":
        depth = cfg.get("max_depth")
        model = RandomForestRegressor(
            n_estimators=200, max_depth=to_int(depth, "max depth", minimum=1) if depth else None,
            n_jobs=-1, random_state=seed,
        )
    elif kind == "gradient_boosting":
        model = HistGradientBoostingRegressor(random_state=seed)
    else:
        raise OpError(f"unknown model '{kind}'")
    model.fit(x.loc[train], y.loc[train])
    pred_col = _new_col(df, cfg.get("prediction_column"), "prediction")
    df[pred_col] = model.predict(x)
    df["split"] = _split_column(df.index, train, test)
    yt, pt = y.loc[test], df.loc[test, pred_col]
    metrics = {
        "task": "regression",
        "model": kind,
        "target": target,
        "r2": round(float(r2_score(yt, pt)), 4),
        "mae": round(float(mean_absolute_error(yt, pt)), 4),
        "rmse": round(float(np.sqrt(mean_squared_error(yt, pt))), 4),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "scored_rows": int((df["split"] == "scored").sum()),
        "features": features,
        "importance": _importances(model, list(x.columns)),
    }
    if notice:
        metrics["notice"] = notice
    return PyResult(df, metrics)


def _regression_schema(cfg: dict[str, Any], schemas: list[Schema]) -> Schema:
    return schemas[0] + [(cfg.get("prediction_column") or "prediction", "DOUBLE"), ("split", "VARCHAR")]


_TARGET = Param("target", "Column to predict", "column", required=True)
_FEATURES = Param("features", "Feature columns", "columns", help="Empty = all usable columns")

register(OpSpec(
    id="predict_regression", label="Predict a number", category="predict", engine="python",
    description="Regression: linear, random forest or gradient boosting; adds a prediction column",
    params=(
        _TARGET, _FEATURES,
        Param("model", "Model", "select", default="random_forest", options=opts(
            ("linear", "linear regression"), ("random_forest", "random forest"),
            ("gradient_boosting", "gradient boosting"))),
        Param("max_depth", "Max tree depth", "integer", show_if=("model", ("random_forest",))),
        *_COMMON,
        Param("prediction_column", "Prediction column", "text", default="prediction"),
    ),
    run=_regression, output_schema=_regression_schema, persist_output=True,
))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classification(dfs: list["pd.DataFrame"], cfg: dict[str, Any]) -> PyResult:
    import pandas as pd
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.tree import DecisionTreeClassifier

    df = dfs[0].copy()
    target = _col(df, cfg.get("target"), "target")
    y = df[target].astype("string")
    labeled_mask = y.notna()
    classes = y[labeled_mask].unique()
    if len(classes) < 2:
        raise OpError(f"'{target}' needs at least two different values to learn from")
    if len(classes) > MAX_CLASSES:
        raise OpError(f"'{target}' has {len(classes)} values; for numbers use 'Predict a number'")
    features = _features(df, cfg, {target})
    x = _encode(df, features)
    labeled = df.index[labeled_mask]
    if len(labeled) < 10:
        raise OpError(f"need at least 10 rows with a value in '{target}'")
    train, test, notice = _split(labeled, cfg, stratify=y.loc[labeled])
    kind = cfg.get("model") or "random_forest"
    seed = to_int(cfg.get("seed", 42), "seed")
    depth = to_int(cfg["max_depth"], "max depth", minimum=1) if cfg.get("max_depth") else None
    if kind == "logistic":
        model: Any = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    elif kind == "decision_tree":
        model = DecisionTreeClassifier(max_depth=depth or 8, random_state=seed)
    elif kind == "random_forest":
        model = RandomForestClassifier(n_estimators=200, max_depth=depth, n_jobs=-1, random_state=seed)
    elif kind == "knn":
        k = to_int(cfg.get("n_neighbors") or 5, "neighbours", minimum=1)
        model = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=k))
    else:
        raise OpError(f"unknown model '{kind}'")
    model.fit(x.loc[train], y.loc[train].astype(str))
    pred_col = _new_col(df, cfg.get("prediction_column"), "prediction")
    df[pred_col] = model.predict(x)
    df["probability"] = model.predict_proba(x).max(axis=1)
    df["split"] = _split_column(df.index, train, test)
    yt, pt = y.loc[test].astype(str), df.loc[test, pred_col].astype(str)
    labels = sorted(pd.unique(pd.concat([yt, pt])))[:20]
    metrics = {
        "task": "classification",
        "model": kind,
        "target": target,
        "accuracy": round(float(accuracy_score(yt, pt)), 4),
        "f1": round(float(f1_score(yt, pt, average="weighted", zero_division=0)), 4),
        "precision": round(float(precision_score(yt, pt, average="weighted", zero_division=0)), 4),
        "recall": round(float(recall_score(yt, pt, average="weighted", zero_division=0)), 4),
        "confusion": {"labels": labels, "matrix": confusion_matrix(yt, pt, labels=labels).tolist()},
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "scored_rows": int((df["split"] == "scored").sum()),
        "features": features,
        "importance": _importances(model, list(x.columns)),
    }
    if notice:
        metrics["notice"] = notice
    return PyResult(df, metrics)


def _classification_schema(cfg: dict[str, Any], schemas: list[Schema]) -> Schema:
    return schemas[0] + [
        (cfg.get("prediction_column") or "prediction", "VARCHAR"),
        ("probability", "DOUBLE"),
        ("split", "VARCHAR"),
    ]


register(OpSpec(
    id="predict_class", label="Predict a category", category="predict", engine="python",
    description="Classification: logistic regression, decision tree, random forest or KNN",
    params=(
        _TARGET, _FEATURES,
        Param("model", "Model", "select", default="random_forest", options=opts(
            ("logistic", "logistic regression"), ("decision_tree", "decision tree"),
            ("random_forest", "random forest"), ("knn", "k nearest neighbours"))),
        Param("max_depth", "Max tree depth", "integer", show_if=("model", ("decision_tree", "random_forest"))),
        Param("n_neighbors", "Neighbours", "integer", default=5, show_if=("model", ("knn",))),
        *_COMMON,
        Param("prediction_column", "Prediction column", "text", default="prediction"),
    ),
    run=_classification, output_schema=_classification_schema, persist_output=True,
))


# ---------------------------------------------------------------------------
# Clustering and anomaly detection
# ---------------------------------------------------------------------------


def _fit_sample(n: int, seed: int):
    import numpy as np

    if n <= MAX_TRAIN_ROWS:
        return None
    return np.sort(np.random.default_rng(seed).choice(n, MAX_TRAIN_ROWS, replace=False))


def _clustering(dfs: list["pd.DataFrame"], cfg: dict[str, Any]) -> PyResult:
    import numpy as np
    import pandas as pd
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    df = dfs[0].copy()
    k = to_int(cfg.get("k") or 3, "number of clusters", minimum=2)
    if len(df) < k:
        raise OpError(f"need at least {k} rows for {k} clusters")
    seed = to_int(cfg.get("seed", 42), "seed")
    features = _features(df, cfg, set())
    x = _encode(df, features)
    xs = StandardScaler().fit_transform(x) if cfg.get("scale", True) else x.to_numpy()
    sample = _fit_sample(len(df), seed)
    model = KMeans(n_clusters=k, n_init=10, random_state=seed)
    model.fit(xs if sample is None else xs[sample])
    col = _new_col(df, cfg.get("cluster_column"), "cluster")
    labels = model.predict(xs)
    df[col] = labels
    sil_idx = np.random.default_rng(seed).choice(len(df), min(len(df), 5000), replace=False)
    sil = (float(silhouette_score(xs[sil_idx], labels[sil_idx]))
           if len(set(labels[sil_idx])) > 1 else None)
    numeric = [f for f in features if pd.api.types.is_numeric_dtype(df[f])][:10]
    profile = df.groupby(col)[numeric].mean().round(4).reset_index().to_dict("records") if numeric else []
    metrics = {
        "task": "clustering",
        "k": k,
        "sizes": {str(i): int(n) for i, n in pd.Series(labels).value_counts().sort_index().items()},
        "silhouette": round(sil, 4) if sil is not None else None,
        "inertia": round(float(model.inertia_), 2),
        "cluster_means": profile,
        "features": features,
    }
    if sample is not None:
        metrics["notice"] = f"fitted on a {MAX_TRAIN_ROWS:,}-row sample"
    return PyResult(df, metrics)


register(OpSpec(
    id="cluster", label="Cluster (k-means)", category="predict", engine="python",
    description="Group similar rows into k clusters; adds a cluster column",
    params=(
        Param("features", "Feature columns", "columns", help="Empty = all usable columns"),
        Param("k", "Number of clusters", "integer", default=3),
        Param("scale", "Scale features", "boolean", default=True),
        Param("seed", "Random seed", "integer", default=42),
        Param("cluster_column", "Cluster column", "text", default="cluster"),
    ),
    run=_clustering,
    output_schema=lambda cfg, s: s[0] + [(cfg.get("cluster_column") or "cluster", "INTEGER")],
    persist_output=True,
))


def _anomaly(dfs: list["pd.DataFrame"], cfg: dict[str, Any]) -> PyResult:
    from sklearn.ensemble import IsolationForest

    df = dfs[0].copy()
    if len(df) < 10:
        raise OpError("need at least 10 rows")
    seed = to_int(cfg.get("seed", 42), "seed")
    features = _features(df, cfg, set())
    x = _encode(df, features)
    raw = cfg.get("contamination")
    if raw in (None, "", "auto"):
        contamination: Any = "auto"
    else:
        contamination = float(to_number(raw, "expected share of anomalies"))
        if not 0 < contamination <= 0.5:
            raise OpError("expected share of anomalies must be between 0 and 0.5")
    sample = _fit_sample(len(df), seed)
    model = IsolationForest(n_estimators=200, contamination=contamination, random_state=seed, n_jobs=-1)
    model.fit(x if sample is None else x.iloc[sample])
    df["anomaly_score"] = -model.score_samples(x)
    df["is_anomaly"] = model.predict(x) == -1
    count = int(df["is_anomaly"].sum())
    metrics = {
        "task": "anomaly",
        "anomalies": count,
        "anomaly_pct": round(100.0 * count / len(df), 2),
        "features": features,
    }
    return PyResult(df, metrics)


register(OpSpec(
    id="detect_anomalies", label="Detect anomalies", category="predict", engine="python",
    description="Isolation forest; adds anomaly_score and is_anomaly",
    params=(
        Param("features", "Feature columns", "columns", help="Empty = all usable columns"),
        Param("contamination", "Expected share of anomalies", "text", default="auto",
              help="'auto' or a number like 0.05"),
        Param("seed", "Random seed", "integer", default=42),
    ),
    run=_anomaly,
    output_schema=lambda cfg, s: s[0] + [("anomaly_score", "DOUBLE"), ("is_anomaly", "BOOLEAN")],
    persist_output=True,
))


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------

_FREQ = {"D": ("D", 7), "W": ("W", 52), "M": ("MS", 12), "Q": ("QS", 4), "Y": ("YS", None)}


def _infer_freq(dates: "pd.Series") -> str:
    diffs = dates.drop_duplicates().sort_values().diff().dt.days.dropna()
    if diffs.empty:
        return "D"
    d = float(diffs.median())
    if d <= 1.5:
        return "D"
    if d <= 10:
        return "W"
    if d <= 45:
        return "M"
    if d <= 135:
        return "Q"
    return "Y"


def _forecast(dfs: list["pd.DataFrame"], cfg: dict[str, Any]) -> PyResult:
    import numpy as np
    import pandas as pd

    df = dfs[0]
    date_col = _col(df, cfg.get("date_column"), "date column")
    value_col = _col(df, cfg.get("value_column"), "value column")
    dates = pd.to_datetime(df[date_col], errors="coerce")
    values = pd.to_numeric(df[value_col], errors="coerce")
    ok = dates.notna() & values.notna()
    if ok.sum() < 3:
        raise OpError("need at least 3 rows with a date and a numeric value")
    dates, values = dates[ok], values[ok]
    freq = cfg.get("frequency") or "auto"
    if freq == "auto":
        freq = _infer_freq(dates)
    if freq not in _FREQ:
        raise OpError(f"unknown frequency '{freq}'")
    alias, default_season = _FREQ[freq]
    how = "mean" if cfg.get("aggregation") == "mean" else "sum"
    ts = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(dates)).sort_index()
    ts = ts.resample(alias).agg(how)
    ts = ts.interpolate() if how == "mean" else ts.fillna(0.0)
    if len(ts) < 3:
        raise OpError(f"only {len(ts)} {freq} periods after grouping; need at least 3")
    horizon = to_int(cfg.get("horizon") or 12, "horizon", minimum=1)
    if horizon > 1000:
        raise OpError("horizon is at most 1000 periods")
    method = cfg.get("method") or "holt_winters"
    season = to_int(cfg["seasonal_periods"], "season length", minimum=2) if cfg.get("seasonal_periods") else default_season
    future = pd.date_range(start=ts.index[-1], periods=horizon + 1, freq=alias)[1:]

    used_season = None
    if method == "holt_winters":
        from statsmodels.tsa.holtwinters import ExponentialSmoothing

        seasonal = "add" if season and len(ts) >= 2 * season else None
        used_season = season if seasonal else None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = ExponentialSmoothing(
                ts, trend="add" if len(ts) >= 4 else None, seasonal=seasonal,
                seasonal_periods=used_season, initialization_method="estimated",
            ).fit()
        fitted = fit.fittedvalues.to_numpy()
        fc = np.asarray(fit.forecast(horizon))
    elif method == "linear_trend":
        x = np.arange(len(ts))
        slope, intercept = np.polyfit(x, ts.to_numpy(), 1)
        fitted = intercept + slope * x
        fc = intercept + slope * np.arange(len(ts), len(ts) + horizon)
    else:
        raise OpError(f"unknown method '{method}'")

    actual = ts.to_numpy()
    resid = actual - fitted
    sd = float(np.nanstd(resid))
    nonzero = actual != 0
    mape = float(np.mean(np.abs(resid[nonzero] / actual[nonzero])) * 100) if nonzero.any() else None
    hist = pd.DataFrame({"period": ts.index, "value": actual, "series": "actual",
                         "lower": np.nan, "upper": np.nan, "fitted": fitted})
    fut = pd.DataFrame({"period": future, "value": fc, "series": "forecast",
                        "lower": fc - 1.96 * sd, "upper": fc + 1.96 * sd, "fitted": np.nan})
    out = pd.concat([hist, fut], ignore_index=True)
    metrics = {
        "task": "forecast",
        "method": method,
        "frequency": freq,
        "seasonal_periods": used_season,
        "history_periods": int(len(ts)),
        "horizon": horizon,
        "mape_pct": round(mape, 2) if mape is not None else None,
        "rmse": round(float(np.sqrt(np.nanmean(resid ** 2))), 4),
    }
    return PyResult(out, metrics)


register(OpSpec(
    id="forecast", label="Forecast", category="predict", engine="python",
    description="Time-series forecast (Holt-Winters or linear trend) with a confidence band",
    params=(
        Param("date_column", "Date column", "column", required=True),
        Param("value_column", "Value column", "column", required=True),
        Param("frequency", "Period", "select", default="auto", options=opts(
            ("auto", "detect"), ("D", "day"), ("W", "week"), ("M", "month"), ("Q", "quarter"), ("Y", "year"))),
        Param("aggregation", "Combine values per period", "select", default="sum", options=opts("sum", ("mean", "average"))),
        Param("horizon", "Periods to forecast", "integer", default=12),
        Param("method", "Method", "select", default="holt_winters", options=opts(
            ("holt_winters", "Holt-Winters (trend + seasonality)"), ("linear_trend", "linear trend"))),
        Param("seasonal_periods", "Season length", "integer",
              help="Periods per cycle; defaults to 7 days, 52 weeks, 12 months, 4 quarters",
              show_if=("method", ("holt_winters",))),
    ),
    run=_forecast,
    output_schema=lambda cfg, s: [("period", "TIMESTAMP"), ("value", "DOUBLE"), ("series", "VARCHAR"),
                                  ("lower", "DOUBLE"), ("upper", "DOUBLE"), ("fitted", "DOUBLE")],
    persist_output=True,
))
