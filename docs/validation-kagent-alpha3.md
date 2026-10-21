# Validation: kagent-dev Substrate v0.5.0-alpha3

Validated on October 14, 2026, using macOS 15.7.7 on AMD64, Docker Desktop 29.8.0, kind 0.31.0, and fresh single-node Linux AMD64 Kubernetes 1.35.0 clusters named `book-alpha3-validation-3` and `book-ch3-6-alpha3-1`. The preexisting `book-workbench` cluster was retained. ARM64, Linux/WSL2 hosts, browsers, and GitHub-hosted CI were not exercised.

## Runtime

- Release: [kagent-dev/substrate v0.5.0-alpha3](https://github.com/kagent-dev/substrate/releases/tag/v0.5.0-alpha3), commit `f499a14475013cf2f8dee8ee4b0f22b47d27047f`.
- Charts: published `substrate-crds`, `substrate-podcert`, and `substrate` OCI charts, version `0.5.0-alpha3`, with archive checksums and OCI digests recorded in `scripts/runtime-lock.json`.
- CLI: all four released host/Linux binaries are recorded in `scripts/cli-checksums.json`. The published binaries report a pseudo-version and the release commit rather than the alpha3 tag, so `ensure_cli` accepts the exact commit embedded in the checksum-verified binary.
- Images: the seven released Substrate images used by the lab, including `objectstore-plugin` and `ateom-gvisor`, are pinned to their multi-platform index digests. The chart's pinned agentgateway image and the third-party lab images remain locked as well.
- Only the companion agent and web application images were built locally. No Substrate source checkout, Go compiler, or runtime image build was required.

## Checks

The complete disposable runner passed:

```shell
KIND_CLUSTER_NAME=book-alpha3-validation-3 ./scripts/test-lab.sh
KIND_CLUSTER_NAME=book-ch3-6-alpha3-1 ./scripts/test-lab.sh
```

All 39 unit and backend HTTP tests passed in both runs. Setup verified the downloaded CLI and charts, rendered every chart image through the digest lock, installed PostgreSQL and the certificate controller, and checked both alpha3 object-store plugin sidecars. The control provider ran beside the API server, and the node provider ran beside atelet. The `generic-worker` WorkerPool resolved its `gvisor` sandbox class to `gvisor-default` before the ActorTemplate was created.

The expanded stateful verifier passed the Chapter 3 setup and walkthrough: locked runtime identity, one ready gVisor Worker, a ready MEMORY template, two initially suspended Actors, independent identity and repository state, stale-step rejection, Worker reuse, suspend/resume restoration, and the exact step 6/step 1 handoff into Chapter 4. It exercised the section 3.7 application actions through the backend while intentionally skipping DOM/browser interaction.

From that preserved state, Chapters 4–6 passed capacity exhaustion, default parking timeout, parked-request handoff, Worker loss and revert/resume, MEMORY/VOLUMES snapshot contents, Pause/Resume behavior, Tag forks and snapshot deletion, the Chapter 5 reset into Chapter 6, UID preservation, HTTP policy readback, and credential-effect admission. This replaces the former full-run pattern that cleaned up a generic deployed smoke test before starting an unrelated Chapter 4–6 suite; `verify-example.sh` remains available as a shorter standalone smoke test.

The observed durable object sets were:

- MEMORY: `checkpoint.img.zstd`, `pages.img.zstd`, `pages_meta.img.zstd`, `fs/fscheckpoint.pb.zstd`, `fs/multitar.img.zstd`, `fs/pages.img.zstd`, `fs/pages_meta.img.zstd`, and `manifest.json`.
- VOLUMES: `fs/fscheckpoint.pb.zstd`, `fs/multitar.img.zstd`, `fs/pages.img.zstd`, `fs/pages_meta.img.zstd`, and `manifest.json`.

Actor status used the alpha3 `status.snapshots` generation list. The suite verified completed durable snapshots through `durableSnapshot.object.snapshotUri`, local Pause snapshots through `locality`, and the recorded `MEMORY` or `VOLUMES` fidelity.

Each runner collected bounded diagnostics, removed its Actors and namespace, deleted its disposable cluster, and restored the previous Kubernetes context. Results are under `.test-results/book-alpha3-validation-3/` and `.test-results/book-ch3-6-alpha3-1/` in the local working tree. After the expanded run, `kind-book-workbench` still had its ready Worker and web application Pods and an empty Actor list.

## Correction discovered during validation

RustFS stores each S3 object in its data directory as internal files such as `xl.meta` and multipart `part.1`. A recursive filesystem listing therefore exposes the storage engine's representation rather than the object names Chapter 5 cares about. The verifier now normalizes those paths to the S3 object boundary before checking the released split-filesystem layout.

## Manuscript alignment found by the expanded run

- The Chapter 3→4 and Chapter 5→6 state handoffs are internally consistent and now have regression coverage. Chapter 3's setup prose and expected CLI version still name alpha2 and its old commit; those need the alpha3 tag, commit, fresh-cluster boundary, and WorkerPool sandbox-class requirement when the manuscript is revised.
- Chapter 4's activation, capacity, parking, and recovery behavior matched alpha3. Its section 4.2 snapshot-status example still uses the removed `status.externalSnapshot` and `contentScope` fields; it must move to the alpha3 `status.snapshots` generation list and `fidelity` terminology with the Chapter 5 rewrite.
- Chapter 5 still needs the tracked fidelity, status, object layout, and object-store ownership rewrite. The alpha3 tests confirm the expected MEMORY and VOLUMES behavior and exact object sets.
- Chapter 6's UID/placement lifecycle and basic HTTP policy shape matched alpha3. The credential example in section 6.4 still describes `injectStaticHeaders`; the released admission shape tested here is the HTTP rule's `replaceHeaders` effect with `header`, `prefix`, and `credentialUri`.
- Browser controls and actual outbound enforcement/credential delivery remain outside this regression.

## Remaining coverage

The browser workflow was intentionally skipped for this run. The API and lifecycle paths behind it passed, but New run, Next step, suspend/resume, the file and diff panels, report display, and deletion still need a separate browser check. Linux/WSL2, ARM64, and GitHub-hosted Linux runs also remain pending.
