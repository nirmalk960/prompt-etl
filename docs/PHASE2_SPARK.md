# Phase 2 — Spark on Kubernetes

This document is the implementation plan for the `SparkEngine` stub in
[backend/prompt_etl/engines/spark_engine.py](../backend/prompt_etl/engines/spark_engine.py).

## Goal

For pipelines whose input exceeds `ENGINE_SPARK_THRESHOLD_BYTES` (default 50
GiB) — or when the user pins `engine: spark` — submit a per-pipeline Spark job
to the **Spark Kubernetes Operator**, with executors sized from the input
Parquet's row-group statistics, and stream stage progress back to the existing
SSE channel.

Per-pipeline isolation gives the "load redistributed per server" property:
each big run gets its own driver + executors, autoscaled by the K8s Cluster
Autoscaler, then released back to the pool when done.

## Cluster prerequisites

| Component | Purpose | Install |
|-----------|---------|---------|
| Spark Kubernetes Operator | Watches `SparkApplication` CRs, creates driver+executor pods | `helm install spark-operator spark-operator/spark-operator` |
| K8s Cluster Autoscaler | Provisions nodes on executor demand | cloud-specific (EKS / GKE / AKS / k3s) |
| IRSA / Workload Identity | Gives driver+executors S3/MinIO credentials without baking secrets | per-cloud |
| (optional) Prometheus + Grafana | Spark metrics scrape via JMX exporter | Helm subchart |

## EngineRouter integration

The router already returns `RoutingDecision(engine="spark", ...)` for big
inputs. In Phase 2 the pipeline executor flips from raising `PipelineError` to
calling `SparkEngine.execute_step(...)`. **No other engine code changes.**

## SparkApplication template

The worker builds and submits a CR like this:

```yaml
apiVersion: sparkoperator.k8s.io/v1beta2
kind: SparkApplication
metadata:
  name: pet-job-{job_id_short}-step-{node_id}
  namespace: prompt-etl-spark
spec:
  type: Python
  pythonVersion: "3"
  mode: cluster
  image: ghcr.io/{org}/prompt-etl-spark-runtime:0.1.0   # PySpark + prompt_etl_runtime wheel
  imagePullPolicy: IfNotPresent
  mainApplicationFile: s3a://prompt-etl/jobs/{job_id}/steps/{node_id}.py
  sparkVersion: "3.5.3"
  arguments:
    - --inputs={comma-separated s3a:// keys}
    - --output=s3a://prompt-etl/jobs/{job_id}/steps/{node_id}.parquet
    - --node-id={node_id}
  hadoopConf:
    "fs.s3a.endpoint": "{S3_ENDPOINT}"
    "fs.s3a.path.style.access": "true"
    "fs.s3a.aws.credentials.provider": "com.amazonaws.auth.WebIdentityTokenCredentialsProvider"
  driver:
    cores: 1
    memory: 2g
    serviceAccount: prompt-etl-spark
  executor:
    cores: 2
    instances: {init_executors}
    memory: {executor_memory}
  dynamicAllocation:
    enabled: true
    initialExecutors: {init_executors}
    minExecutors: 2
    maxExecutors: {max_executors}
  sparkConf:
    "spark.kubernetes.executor.deleteOnTermination": "true"
    "spark.eventLog.enabled": "true"
    "spark.eventLog.dir": "s3a://prompt-etl/spark-history/"
```

## Adaptive executor sizing

```python
def size_executors(input_bytes: int, node_kind: str, node_alloc_bytes: int):
    target_partition = 256 * 1024 * 1024  # 256 MiB
    inflation = 4 if node_kind in {"aggregate", "deduplicate"} else 2
    init = max(2, math.ceil(input_bytes / target_partition / 4))
    cap = max(4, min(200, math.ceil(input_bytes / target_partition)))
    mem_per_exec = max(4 * 1024**3, min(node_alloc_bytes * 0.8, target_partition * inflation * 8))
    return init, cap, _round_mem(mem_per_exec)
```

The worker reads the Parquet file footer (via `pyarrow.parquet.read_metadata`)
to get row-group sizes — that's far more accurate than guessing from file size
alone, especially for columnar formats with column pruning.

## LLM templates

Add a fourth template family in
[backend/prompt_etl/services/operation_templates.py](../backend/prompt_etl/services/operation_templates.py):

```python
_SPARK_SYSTEM = """You are a precise PySpark code generator.
Rules:
- Output ONLY a Python code block.
- Inputs are `inputs` (list of pyspark.sql.DataFrames) and `df` (= inputs[0]).
- Assign the final DataFrame to `result`.
- `spark` (SparkSession) and `F` (pyspark.sql.functions) are pre-imported.
- Never call `.toPandas()` on the full DataFrame; use `.limit(N).toPandas()` for previews only.
- Use the DataFrame API or Spark SQL (`spark.sql(...)`).
"""
```

Update `agent_guard.validate("spark", code)` to:

1. Run the same Python-AST checks as `validate_python`.
2. Reject `.toPandas()` outside of `.limit(...).toPandas()` patterns.
3. Reject `spark.stop()`, `SparkContext._jvm`, etc.

## Driver progress → Redis pubsub

The driver image includes a small `progress_listener.py` that registers a
`SparkListener` (via `spark.sparkContext._gateway`) which forwards stage start,
stage end, and task counts to Redis (using the same `job:{id}` channel). The
existing SSE endpoint streams these to the UI without modification.

Failed tasks emit a `step_warning` event; pipeline failure emits the standard
`job_failed`.

## Spark UI reverse proxy

Add `routers/spark_ui.py` exposing `GET /jobs/{id}/spark-ui/{path:path}`. It:

1. Looks up the driver pod name from the SparkApplication status.
2. Port-forwards (or proxies via the in-cluster service) `:4040` → request.
3. Rewrites HTML links so the embedded Spark UI works in an iframe.

The frontend gets a "Open Spark UI" button on the JobDetail page whenever
`job.engine === "spark"`.

## Test plan

- Unit: `size_executors()` math.
- Integration with `minikube` + Spark Operator: submit a sample 1 GB Parquet
  job, assert event sequence and final dataset row count.
- Soak: 30 GB synthetic dataset on a 3-node cluster; assert dynamic allocation
  scales executors and the K8s autoscaler adds nodes.
