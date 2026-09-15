# ADR 0010 — Explicit storage backend: local development cannot reach real AWS

- **Status:** Accepted
- **Date:** 2026-09-14
- **Milestone:** Between M4 and M5 (safety design)

## Context

An audit after M4 confirmed that no AWS account had been used: S3 was the
local RustFS container and DynamoDB was DynamoDB Local. It also found a latent
risk. The development machine holds real AWS credentials in
`~/.aws/credentials`, and the code decided "local or AWS" implicitly:

```python
boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None, ...)
#                                                       ^^^^^^^ empty → real AWS
```

One empty `S3_ENDPOINT_URL`, or one empty key, would have made boto3 fall back
to real AWS using whatever credentials it found on the machine, silently
uploading meeting recordings to a real account.

## Decision

The target is an explicit setting, `STORAGE_BACKEND`, and every AWS SDK client
is created in one module, `app/core/aws_clients.py`.

### `STORAGE_BACKEND=local` (development, the default)

Four independent layers, each tested and mutation-checked:

| Layer | Where | What it stops |
|---|---|---|
| **1. Start-up validation** | `Settings` validator | App refuses to start if any S3/DynamoDB endpoint is empty, an AWS domain (`*.amazonaws.com`, `.com.cn`, `*.api.aws`), a non-loopback IP, or an external hostname; or if explicit keys are missing. All problems are reported together. |
| **2. Client construction** | `build_client()` | Re-checks the endpoint and keys, so a client cannot be built unsafely even outside `Settings`. |
| **3. Isolated SDK session** | `_isolated_session()` | Shared credentials and config files point at the null device; `AWS_PROFILE` is ignored. `~/.aws` is never read, and profile settings cannot redirect endpoints or regions. |
| **4. Request guard** | `before-send` hook | Every outgoing request's host must equal the configured endpoint's host, or it is refused before a connection opens. The last line of defence against any future bug. |

A fifth, structural check: a test fails if any module other than
`aws_clients.py` creates a boto3 client, resource, or session.

Allowed local hosts: `localhost`, loopback addresses, `host.docker.internal`,
and single-label names (Docker Compose services such as `object-storage`).

### `STORAGE_BACKEND=aws` (deployment)

Designed here: real S3 and DynamoDB, with credentials from the normal chain (an
IAM role on AWS compute), and endpoints empty or omitted. It is
guarded: selecting it is an explicit, deliberate configuration change made
alongside Terraform-managed infrastructure.

`local` is the default, so a missing variable is safe rather than dangerous.

## Evidence

- 54 tests in `tests/test_storage_safety.py`: AWS endpoints rejected for all
  three endpoint settings, empty endpoints and missing keys rejected, local
  hosts accepted, `aws` mode refused, and clients built on a machine simulating
  real credentials in `~/.aws/credentials`, `AWS_CONFIG_FILE`, and
  `AWS_PROFILE` still using only the explicit local keys and endpoint.
- **Mutation check:** removing each protection breaks tests: request guard
  (2 fail), AWS-domain rejection (26), null credentials file (1), required
  keys (1).
- The tests never aim a request at an AWS host. The guard test pins a
  localhost client to a different allowed host, so it proves blocking on the
  real send path without any possibility of contacting AWS.

**Found while building this:** with `AWS_PROFILE` set in the shell, the
isolated session crashed with `ProfileNotFound`. The profile lookup from the
environment is now disabled, and a test covers it.

## Alternatives considered

**Rely on `.env` containing the right values.** That was the previous state.
Rejected: the failure mode was silent, and the consequence was data leaving
the machine.

**Unset `AWS_*` variables at start-up.** Does not cover `~/.aws/credentials`
or `~/.aws/config`, and mutating the process environment affects other
libraries.

**A separate fake storage implementation for development.** Rejected: it would
stop the development path exercising the real S3 protocol (presigned POST
policies, ranged reads), which the M4 tests depend on.

## Consequences

**Advantages**
- A misconfiguration fails loudly at start-up instead of reaching AWS.
- Local development is safe on a machine that also holds real AWS credentials.
- The switch to AWS is one explicit, reviewable change.

**Limitations**
- Relies on botocore's `session_var_map` to ignore `AWS_PROFILE`. That is an
  internal-ish attribute and could change across botocore versions; a test
  catches it if it does.
- Single-label hostnames are trusted as Docker service names. A single-label
  name that resolves externally via a DNS search domain would pass layer 1,
  though layer 4 still pins requests to that exact host.
