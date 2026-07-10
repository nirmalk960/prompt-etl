"""Phase 2 stub: Spark on Kubernetes engine.

This module deliberately does not import pyspark or kubernetes clients yet —
those become heavy dependencies only when Phase 2 is built out. The class
exists so the router and tests can reference the EngineKind="spark" surface
and so the implementation plan is unambiguous.

Planned implementation (see docs/PHASE2_SPARK.md):

1. Worker writes the generated PySpark code to S3 as `pipeline_<step>.py`.
2. Worker creates a SparkApplication CR via the kubernetes client:
       sparkoperator.k8s.io/v1beta2 SparkApplication
   with:
       - driver image: prompt-etl-spark-runtime:<tag>
       - mainApplicationFile: s3a://.../pipeline_<step>.py
       - dynamicAllocation: enabled with min/max executors sized
         from input row-group stats
       - hadoopConf for S3 credentials (IRSA in EKS / ServiceAccount in GKE)
3. Worker watches the SparkApplication status; streams driver log lines and
   stage progress to Redis pubsub `job:{id}` so the existing SSE UI works.
4. On success, the Spark job has already written the output parquet to S3 at
   `output_object_key`. Worker just records row_count / byte_size and emits
   the preview by sampling with DuckDB.

The Spark UI is reverse-proxied at /jobs/{id}/spark-ui/* in the API gateway.
"""

from __future__ import annotations

from typing import Any

from .base import DataEngine, StepPreview, StepResult


class SparkEngine(DataEngine):
    kind = "spark"

    async def execute_step(
        self,
        *,
        node_id: str,
        node_kind: str,
        code: str,
        input_object_keys: list[str],
        output_object_key: str,
        config: dict[str, Any] | None = None,
    ) -> StepResult:
        raise NotImplementedError(
            "SparkEngine is a Phase 2 stub. See docs/PHASE2_SPARK.md for the "
            "implementation plan. For Phase 1, raise the engine_spark_threshold_bytes "
            "setting or pin a single-node engine."
        )

    async def preview(self, *, object_key: str, n: int = 50) -> StepPreview:
        raise NotImplementedError("SparkEngine is a Phase 2 stub.")
