# Credential brokering walkthrough

A manual, step-by-step run of the Chapter 6 credential brokering demo on the `book-v060-cred` lab cluster. Every command names its cluster with `--context`, so your current kube context is never changed. Run everything from the `book-src` directory.

What you are proving: `repo-workbench-1` publishes its `AGENT_REPORT.md` to an internal source-control service that requires a bearer token, and the Actor never holds that token. It sends `Authorization: placeholder`; AgentGateway replaces the value only when the Actor's `EgressPolicy` says so, only for a Secret the platform operator granted to the Atespace, and only on requests that carry the header. A second header source delivers a Substrate-minted Actor JWT that the service verifies.

## 0. Shell setup

```shell
cd ~/go/src/github.com/agent-substrate/book-src
export CTX=kind-book-v060-cred
export PATH="$PWD/.bin:$PATH"

# Call the companion backend from inside the cluster (the Chapter 3 bridge).
# Usage: wb <path> [GET|POST|DELETE] [json-body]
wb() {
  kubectl --context "$CTX" exec -n book-workbench deployment/repo-workbench-webapp -- python3 -c '
import json, sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
path, method, body = sys.argv[1:]
request = Request("http://127.0.0.1" + path, data=body.encode() if body else None,
                  method=method, headers={"Content-Type": "application/json"})
try:
    response = urlopen(request, timeout=600)
except HTTPError as error:
    response = error
with response:
    print(response.read().decode())
' "$1" "${2:-GET}" "${3:-}"
}

# Publish the report from an Actor. Usage: publish <actor> [assert]
publish() {
  wb "/api/actors/$1/publish" POST "{\"assertActor\": $([[ "${2:-}" == assert ]] && echo true || echo false)}" |
    jq '.publish'
}
```

Confirm the lab is healthy and empty:

```shell
kubectl --context "$CTX" -n ate-system get deploy atenet-egress k8s-credential-provider ate-idp-server
kubectl --context "$CTX" -n book-workbench get pods
kubectl ate --context "$CTX" get actors -a book-workbench
```

Expect three ready deployments, three running Pods (`generic-worker-*`, `repo-workbench-webapp-*`, `source-control-*`), and no Actors. If Actors exist from an earlier run, delete them first (see section 10).

The source-control service keeps its pull records in process memory, so a service Pod that served an earlier run still lists those pulls. Start from a fresh service so the counts below match:

```shell
kubectl --context "$CTX" -n book-workbench rollout restart deployment/source-control
kubectl --context "$CTX" -n book-workbench rollout status deployment/source-control --timeout=120s
```



## 1. What setup prepared

The platform operator's grant, applied through the chart value `credentialProvider.namespacePolicies`:

```shell
kubectl --context "$CTX" -n ate-system get configmap k8s-credential-provider-namespace-policy \
  -o jsonpath='{.data.namespace-policy\.yaml}'; echo
```

Expect `policies: [{"allowedNamespaces":["workbench-secrets"],"atespace":"book-workbench"}]`.

The account token lives in exactly two Secrets: the credential store the provider reads, and the service's own copy. Record its fingerprint; you will match it later without ever printing the token.

```shell
kubectl --context "$CTX" get secret git-token -n workbench-secrets -o jsonpath='{.data.token}' |
  openssl base64 -d -A | shasum -a 256 | cut -c1-16
```

The service has received nothing yet:

```shell
wb /api/source-control/pulls | jq
```

Expect `{"pulls": []}`. That listing is the only thing the web app can read from the service; it never carries a token.

## 2. Create and complete an Actor

```shell
kubectl ate --context "$CTX" create actor repo-workbench-1 -a book-workbench --template repo-workbench
kubectl ate --context "$CTX" resume actor repo-workbench-1 -a book-workbench
wb /api/actors/repo-workbench-1/fast-forward POST '{"expectedStep": 0}' |
  jq '{phase, currentStep, canPublish, actor}'
```

Expect `phase: "done"`, `currentStep: 7`, `canPublish: true`, and the Actor's Atespace, name, and UID. Note the UID; the Actor JWT in section 7 must carry the same value.

Publishing is available from step five onward (once the report exists). It is an action, not a step: the step count never changes below.

## 3. No policy: denied before it leaves the sandbox

```shell
publish repo-workbench-1
```

Expect `status: null` and an error like `URLError: <urlopen error [Errno -2] Name or service not known>`. The Actor has no `EgressPolicy`, so the DNS relay refuses to resolve the service name. Nothing reached the gateway in the last minute:

```shell
kubectl --context "$CTX" logs -n ate-system deployment/atenet-egress --since=1m | grep -c 'ate.actor.name=repo-workbench-1'
```

Expect `0`. Resuming and fast-forwarding the Actor go through the router, not the egress gateway, so they never appear here.

## 4. Allow the destination, no credential

```shell
kubectl ate --context "$CTX" create egress-policy repo-workbench-1 -a book-workbench -f - <<'EOF'
rules:
- http:
    hostnames:
    - source-control.book-workbench.svc.cluster.local
EOF
publish repo-workbench-1
```

Expect `status: 401` with `"error": "Authorization header is not a bearer token"`. The request reached the service carrying the literal word `placeholder`. Allowing a destination and granting a credential are separate decisions.

A new or replaced policy takes a few seconds to reach the DNS relay and the gateway. Whenever a publish right after a policy change returns the DNS error from section 3, run `publish` again. This applies to every policy change below.

## 5. Inject the credential

`kubectl ate update egress-policy` requires the stored `uid` and `version`, so the simplest manual path is delete and recreate.

```shell
kubectl ate --context "$CTX" delete egress-policy repo-workbench-1 -a book-workbench
kubectl ate --context "$CTX" create egress-policy repo-workbench-1 -a book-workbench -f - <<'EOF'
rules:
- http:
    hostnames:
    - source-control.book-workbench.svc.cluster.local
    effects:
      replaceHeaders:
      - header: Authorization
        prefix: "Bearer "
        credentialUri: ate-secret://k8s.io/default/workbench-secrets/git-token/token
EOF
publish repo-workbench-1
```

Expect `status: 201`, `sentHeaders: {"Authorization": "placeholder"}`, and a response with `authenticatedAs: "repo-workbench-bot"` and `actor: null`.

Now compare the three views of that one request.

The Actor's view. Its transcript shows what it sent, which is still the placeholder:

```shell
wb /api/actors/repo-workbench-1/state | jq -r '.transcript[-1].body'
```

The service's view. It authenticated an account and recorded a token fingerprint. Compare it with the fingerprint from section 1:

```shell
wb /api/source-control/pulls | jq '.pulls[-1] | {number, authenticatedAs, tokenFingerprint, actor}'
```

The gateway's view. The structured access log names the Actor on the request. The gateway also logs DNS resolver debug lines by the hundred, so search by time rather than by line count:

```shell
kubectl --context "$CTX" logs -n ate-system deployment/atenet-egress --since=10m |
  grep 'ate.actor.name=repo-workbench-1' | grep 'http.status=' | tail -1
```

Expect one `info request` line with `http.status=201`, `ate.actor.name=repo-workbench-1`, the Actor's UID, and `ate.atespace=book-workbench`. The gateway's call to the credential provider for that same request is logged just before it:

```shell
kubectl --context "$CTX" logs -n ate-system deployment/atenet-egress --since=10m |
  grep 'FetchSecret' | tail -1
```

Expect an `upstream request` line to `k8s-credential-provider.ate-system.svc:50051` with `http.status=200`. That is the only place the real token travels: from the provider to the gateway, over mTLS, after the request left the sandbox.

## 6. The provider boundary

Point the same rule at a Secret in a namespace the Atespace was not granted. The Secret exists, which makes the point sharper: the provider refuses based on the grant, not on existence.

```shell
kubectl ate --context "$CTX" delete egress-policy repo-workbench-1 -a book-workbench
kubectl ate --context "$CTX" create egress-policy repo-workbench-1 -a book-workbench -f - <<'EOF'
rules:
- http:
    hostnames:
    - source-control.book-workbench.svc.cluster.local
    effects:
      replaceHeaders:
      - header: Authorization
        prefix: "Bearer "
        credentialUri: ate-secret://k8s.io/default/book-workbench/source-control-token/token
EOF
publish repo-workbench-1
```

Expect `status: 403` and the response `actor egress policy denied`. The Actor learns only that it was refused; the gateway records why:

```shell
kubectl --context "$CTX" logs -n ate-system deployment/atenet-egress --since=1m |
  grep 'http.status=403' | grep -o 'error=.* reason=[A-Za-z]*'
```

Expect `error="credential provider k8s.io denied: ...` with the provider's message, `atespace "book-workbench" is not permitted to resolve secrets in namespace "book-workbench"` (its quotes escaped), followed by `reason=Authorization`. The gateway failed closed; confirm the service did not receive a new pull:

```shell
wb /api/source-control/pulls | jq '.pulls | length'
```

Expect `1`.

### What the grant is, and is not

The grant is the provider's namespace policy: the `credentialProvider.namespacePolicies` Helm value, rendered into the `k8s-credential-provider-namespace-policy` ConfigMap you read in section 1. It says one thing: Actors in Atespace X may resolve Secrets in Kubernetes namespaces Y and Z. It is default-deny, and the provider checks it on every `FetchSecret` call using the Actor identity the gateway asserts. The Secret in this step exists and the provider's ServiceAccount could read it, so the refusal comes from the grant alone.

It is a validity check, not a lease. It has no time to live, expiration, use count, or revocation handle, and it is coarse in three ways:

- Scope is the whole namespace. A grant covers every Secret and every key there. You cannot grant one Secret.
- Subject is the Atespace, not the Actor. Every Actor in `book-workbench` gets the same answer. The provider receives `spiffe://substrate-actor.local/actor/<atespace>/<name>` with no UID, so it cannot tell a recreated Actor at the same name from its predecessor.
- It is loaded when the provider starts. Changing the Helm value rolls the provider; editing the ConfigMap by hand needs a restart.

Three separate decisions gate one injected request, and none of them carries a lifetime:

| Decision | Who decides | What it answers |
| --- | --- | --- |
| May this policy name that URI? | `ate-api-server`, through its OpenFGA roles | Who may write `repo-workbench-1`'s `EgressPolicy` at all |
| May this Atespace resolve that namespace? | The credential provider's grant | This step's refusal |
| May the provider read that Secret? | Kubernetes RBAC on the provider's ServiceAccount | Cluster-wide `get` in the published chart |

Time enters in two places only. The static Secret has no expiry of its own; the destination decides how long the token is good. The gateway caches a resolved credential for 300 seconds per Actor and URI, so a rotation (section 9) and a revocation both take up to five minutes to reach injected requests. If you removed `workbench-secrets` from the grant and rolled the provider, an Actor that had already published would keep receiving the cached token for up to that long. The `actorJwt` source in section 7 is the one place the policy writer sets a lifetime: `expirationSeconds` is required, bounded to 300 through 3600, becomes the token's `exp` claim, and the gateway caches the minted JWT for two thirds of it. That is a property of the minted assertion, not of any grant.

## 7. Assert the Actor with a Substrate-minted JWT

The injected token identifies the `repo-workbench-bot` account, not the Actor. Add a second header source. The gateway asks the control plane to mint a JWT for this Actor, bound to the audience you name, and injects it into `Actor-Assertion`. The service verifies the ES256 signature against `https://idp.ate-system.svc`, checks the audience, and records the claims.

```shell
kubectl ate --context "$CTX" delete egress-policy repo-workbench-1 -a book-workbench
kubectl ate --context "$CTX" create egress-policy repo-workbench-1 -a book-workbench -f - <<'EOF'
rules:
- http:
    hostnames:
    - source-control.book-workbench.svc.cluster.local
    effects:
      replaceHeaders:
      - header: Authorization
        prefix: "Bearer "
        credentialUri: ate-secret://k8s.io/default/workbench-secrets/git-token/token
      - header: Actor-Assertion
        actorJwt:
          audiences:
          - source-control.book-workbench.svc.cluster.local
          expirationSeconds: 600
EOF
publish repo-workbench-1 assert
```

Expect `status: 201`, `sentHeaders` with both headers set to `placeholder`, and `response.actor` holding `subject: "actor/book-workbench/repo-workbench-1"`, the Atespace, the name, and the UID from section 2.

Two controls worth running:

```shell
# Without the header, no assertion is injected: the gateway replaces only headers the Actor sends.
publish repo-workbench-1 | jq '{status, actor: .response.actor}'
```

Expect `201` and `actor: null`.

```shell
# A placeholder assertion that the gateway does not replace is rejected by the service.
# Repeat the publish if the first attempt after the policy change returns the DNS error.
kubectl ate --context "$CTX" delete egress-policy repo-workbench-1 -a book-workbench
kubectl ate --context "$CTX" create egress-policy repo-workbench-1 -a book-workbench -f - <<'EOF'
rules:
- http:
    hostnames:
    - source-control.book-workbench.svc.cluster.local
    effects:
      replaceHeaders:
      - header: Authorization
        prefix: "Bearer "
        credentialUri: ate-secret://k8s.io/default/workbench-secrets/git-token/token
EOF
publish repo-workbench-1 assert | jq '{status, response}'
```

Expect `401` with `"Actor-Assertion rejected: malformed token: Not enough segments"`. Recreate the two-header policy from the top of this section before continuing.

## 8. Identity follows the Actor

```shell
kubectl ate --context "$CTX" suspend actor repo-workbench-1 -a book-workbench --mode sync
kubectl ate --context "$CTX" get actor repo-workbench-1 -a book-workbench -o json | jq '{state: .status.state, workerPod: .status.workerAssignment.workerPod}'
kubectl ate --context "$CTX" resume actor repo-workbench-1 -a book-workbench
publish repo-workbench-1 assert | jq '{status, actor: .response.actor}'
wb /api/actors/repo-workbench-1/state | jq '.publications | length'
```

Expect the suspended Actor to show `workerPod: null`, the next publish to return `201` with the same UID, and the publication history to include every earlier attempt. The policy, the credential, and the Actor's memory of its attempts all came back with the Actor; none of it belonged to the Worker.

A second Actor with no policy still cannot reach the service at all:

```shell
kubectl ate --context "$CTX" suspend actor repo-workbench-1 -a book-workbench --mode sync
kubectl ate --context "$CTX" create actor repo-workbench-2 -a book-workbench --template repo-workbench
kubectl ate --context "$CTX" resume actor repo-workbench-2 -a book-workbench
wb /api/actors/repo-workbench-2/fast-forward POST '{"expectedStep": 0}' | jq '.canPublish'
publish repo-workbench-2 | jq '{status, error}'
```

Expect the DNS error from section 3. Both Actors ran on the same Worker Pod; only the policy attached to the Actor differed.

## 9. Optional: rotate the token

Rotate the account token in both Secrets without touching either Actor. The service re-reads its mounted file, which the kubelet refreshes within about a minute. The gateway caches a resolved credential for up to five minutes per Actor and URI, so the first publishes after rotation may return `401` until that cache expires. That delay is a property of the released gateway, not a bug in the lab.

```shell
new_token="$(openssl rand -hex 24)"
for ns_secret in workbench-secrets/git-token book-workbench/source-control-token; do
  kubectl --context "$CTX" create secret generic "${ns_secret#*/}" -n "${ns_secret%/*}" \
    --from-literal=token="$new_token" --dry-run=client -o yaml | kubectl --context "$CTX" apply -f -
done
unset new_token
```

Then, after resuming `repo-workbench-1`, run `publish repo-workbench-1` every 15 seconds or so. In a measured run, the first two publishes still returned `201` because both sides held the old token, the service switched to the new token after about 30 seconds and began answering `401` while the gateway kept injecting its cached old value, and the gateway's cache expired about four minutes after rotation, at which point publishes returned `201` with the new fingerprint in the service's record. Neither Actor was touched.

## 10. Browser check and cleanup

The UI exposes the same action. Keep this running in a second terminal:

```shell
KIND_CLUSTER_NAME=book-v060-cred ./scripts/open-workbench.sh
```

Open [http://localhost:8080](http://localhost:8080), select `repo-workbench-1`, and use **Publish report** with and without **Assert Actor** checked. The notice line reports the HTTP status the service returned, and the transcript shows each attempt's placeholder headers.

Delete the Actors when you are done. Deleting an Actor also deletes its egress policy.

```shell
kubectl ate --context "$CTX" delete actor repo-workbench-2 -a book-workbench
kubectl ate --context "$CTX" delete actor repo-workbench-1 -a book-workbench
kubectl ate --context "$CTX" get actors -a book-workbench
```



## Troubleshooting

- **Still a DNS error right after creating a policy.** The relay and gateway pick up policies asynchronously. Wait a few seconds and publish again.
- **An unexpected `403`.** The Actor always sees `actor egress policy denied`; read the `error=` field on the matching `atenet-egress` log line for the reason.
- `403` **logged as "no credential provider configured".** The egress gateway config lost its `credentialProviders` entry; the published chart wires it by default. Check `kubectl --context "$CTX" -n ate-system get configmap atenet-egress-agentgateway-config -o yaml`.
- `403` **logged as naming your Atespace on a granted namespace.** The provider loads grants at startup. Check the ConfigMap from section 1 and the provider's Pod age; a Helm values change rolls it automatically.
- `503` **from the gateway.** The provider or the control plane was unreachable. It is fail-closed and retryable; check `kubectl --context "$CTX" -n ate-system get pods`.
- **Actor JWT rejected with "signing key unavailable".** The service could not reach `https://idp.ate-system.svc` or did not trust its certificate. Check the `ate-idp-server` Pods and the projected `servicedns.podcert.ate.dev` trust bundle in the `source-control` Pod.

