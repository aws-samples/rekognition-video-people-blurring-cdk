# Repository modernization plan

All changes are delivered in one PR, preserving the stack name, existing bucket
construct IDs, input formats, output keys, and face-blurring workflow.

1. **P0 — Supported deployment and dependencies** ([#18](https://github.com/aws-samples/rekognition-video-people-blurring-cdk/issues/18), [#14](https://github.com/aws-samples/rekognition-video-people-blurring-cdk/issues/14)): Completed.
   Upgrade all application Lambdas to Python 3.14, update CDK and its CLI, remove
   obsolete dependencies and packaging, and lock compatible installations.
2. **P1 — Correct video processing** ([#10](https://github.com/aws-samples/rekognition-video-people-blurring-cdk/issues/10), [#19](https://github.com/aws-samples/rekognition-video-people-blurring-cdk/issues/19)): Completed.
   Clamp border detections, retain fractional frame rates and optional audio,
   surface processing failures, handle every S3 record, and store large detection
   results outside Step Functions payloads.
3. **P2 — Regression protection and documentation** ([#20](https://github.com/aws-samples/rekognition-video-people-blurring-cdk/issues/20)): Completed.
   Add handler, media, and infrastructure tests; run lint, dependency audits,
   CDK synthesis, and a real Lambda container smoke test; refresh setup,
   deployment, limits, and cleanup documentation.
4. **Delivery and backlog closure**: In progress.
   Merge the validated consolidated PR, close resolved issues and superseded
   PRs #11, #12, #13, and #17 with links to the replacement, and verify that no
   open issues or PRs remain.

## Validation

- 40 unit/regression tests pass on Python 3.14, including a resource-ID baseline
  from `main` at `bf7c162` synthesized with CDK 2.103.1.
- Five real media scenarios pass inside the Python 3.14 Lambda image: MP4 and MOV,
  with and without audio, plus no detected faces. The image runs without network
  access, as a non-root user, with a read-only filesystem and writable `/tmp`.
- Ruff lint/format, dependency consistency, Python dependency audits (deployment,
  container, and development), npm audit, and CDK synthesis pass.
- Live AWS deployment is not part of these checks. Review `cdk diff` in the target
  account and test a representative clip when deploying.
