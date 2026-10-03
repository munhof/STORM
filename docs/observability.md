# Execution observability

`storm.observability.ExecutionObserver` is independent of Django and scientific libraries.
It records version 1 events with UTC timestamps, component identity/version, operation,
span identifier, status, elapsed seconds and error type/message. Calling an operation
records its start and completion or failure without storing input arrays.

Reflection discovers the optional `set_progress_callback(callback)` capability. Components
report internal counters; reflection cannot infer percentages for opaque operations.
Batch events support `batch_step`, `batch_total`, `processed_observations`,
`total_observations`, `throughput`, `batch_eta_seconds`, `epoch` and `device`.
The observer throttles intermediate model batches to once per second and keeps boundaries.
Callbacks are detached before saving a model.

Studio persists bounded activity (100 events, 20 stage transitions), execution identity,
current progress and failure context. Changing phases clears stale batch indicators.
Pipeline steps receive a callback on `PipelineContext`, outside serializable metadata.
The runner observes step lifecycle automatically. Connectors and metrics executed by the
suite also produce lifecycle events. Materialized preparation forwards step activity.

RAINSTORM native VAME reports training and encoding/inference batches; GPU pose
transformations report completed coordinate blocks. A completed batch is not a recovery
checkpoint. Recovery uses successfully saved checkpoints, currently epoch boundaries.
Official VAME and external supervised processes need their own internal callbacks before
Studio can show real batch percentages. Synchronous UI actions are not yet all covered
by this observer. This is a local event contract, not an OpenTelemetry exporter.

Running workers must load the new code to produce these events; updating source files
does not modify an already running Python process. Schedule worker reload between jobs.

## Progress protocol and checkpoints

`storm.contracts.ProgressReporter` documents the existing optional callback capability;
`ExecutionObserver.bind` and `storm.learning` checkpoint interfaces remain compatible.
It adds no new heartbeat or ETA semantics. Phase/batch/session normalization and
historical checkpoint retention remain pending in the [current plan](planning/suite-completion.md).
