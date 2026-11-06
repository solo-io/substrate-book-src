# OpenBao credential brokering walkthrough

A manual, step-by-step run of the credential brokering demo with the account token in [OpenBao](https://openbao.org) instead of a Kubernetes Secret. It follows [the Kubernetes Secrets walkthrough](credential-brokering-walkthrough.md); read that first. Every command names its cluster with `--context`, so your current kube context is never changed. Run everything from the `book-src` directory.

Two OpenBao providers can serve the same story, and this walkthrough runs both:

- `openbao`, the default, logs in to OpenBao as its own ServiceAccount through OpenBao's Kubernetes auth method. It decides itself which Atespace may read which secret, as the bundled `k8s.io` provider does, and tells OpenBao which Actor each read is for in a request header that OpenBao's audit log records. It needs nothing from Substrate beyond a place in the egress gateway's provider list.
- `openbao-actor` logs in to OpenBao **as each Actor**, with a Substrate Actor JWT it mints through `ate-api-server`. OpenBao's policy decides what each Actor may read, and OpenBao's audit log names the Actor's own entity. Minting needs Substrate's `egress_gateway` role, which this variant gets from a Helm values overlay.

In both, the Actor sends only `Authorization: placeholder`.

## 0. Install and shell setup

Install the broker once per lab. Setup upgrades the `substrate` release with `manifests/substrate-values-openbao-actor.yaml` (the role `openbao-actor` needs), deploys a development OpenBao and both providers (built from the pinned `../credprovider-openbao` checkout), and adds both to the egress gateway.

```shell
cd ~/go/src/github.com/agent-substrate/book-src
export KIND_CLUSTER_NAME=book-v100
./scripts/setup-openbao.sh
./scripts/prep-openbao-demo.sh
source ./scripts/demo-env.sh

# The OpenBao CLI inside the development OpenBao, as its root token. Lab only.
bao() {
  kubectl --context "$CTX" exec -i -n openbao deployment/openbao -- \
    env BAO_ADDR=http://127.0.0.1:8201 BAO_TOKEN=root bao "$@"
}
```

`prep-openbao-demo.sh` does everything `prep-credential-demo.sh` does (no Actors, a fresh source-control service, an empty gateway cache), then stores the account token in OpenBao and a different token for another Atespace, `team-a`.

The development OpenBao keeps its state in memory and starts unsealed with a fixed root token. If its Pod restarts, rerun `prep-openbao-demo.sh`.

## 1. What setup prepared

The `openbao` provider's identity holds no Substrate role. `ate-api-server` grants `egress_gateway` to the gateway and the `openbao-actor` provider only:

```shell
kubectl --context "$CTX" -n ate-system get deployment ate-api-server -o json |
  jq -r '.spec.template.spec.containers[0].args[] | select(startswith("--egress-gateway-spiffe-id"))'
```

Expect `.../sa/atenet-egress` and `.../ns/credprovider-openbao-actor/sa/openbao-credential-provider`. Nothing names `credprovider-openbao`.

On the OpenBao side, setup configured three things for the `openbao` provider: a way to log in, a policy that says what that login may read, and an audit setting that records which Actor each read is for.

### How the provider logs in

Every OpenBao client logs in through an auth method, mounted at a path under `auth/`. The client presents a credential the method understands and gets back an OpenBao token, which carries the policies it may use. List the methods:

```shell
bao auth list
```

Expect `kubernetes/`, which the `openbao` provider uses; `jwt/`, which `openbao-actor` uses in section 5; and `token/`, OpenBao's built-in method, which the root token in the `bao` function belongs to.

The Kubernetes method accepts a Kubernetes ServiceAccount token. OpenBao does not check the token's signature itself: it sends the token to the Kubernetes API server in a TokenReview, and the API server says whether it is valid and which ServiceAccount it belongs to. OpenBao makes that call with its own Pod's ServiceAccount token, which is allowed to create TokenReviews only because setup bound OpenBao's ServiceAccount to the `system:auth-delegator` ClusterRole:

```shell
bao read auth/kubernetes/config
kubectl --context "$CTX" get clusterrolebinding openbao-tokenreview \
  -o jsonpath='{.roleRef.name} {.subjects[0].namespace}/{.subjects[0].name}{"\n"}'
```

Expect `kubernetes_host` `https://kubernetes.default.svc` and `token_reviewer_jwt_set` `false`, which means OpenBao reviews with its own Pod's token, and then `system:auth-delegator openbao/openbao`.

A valid ServiceAccount token is not enough: a role decides which ServiceAccounts may log in and what they get. The `substrate-credential-provider` role:

```shell
bao read -format=json auth/kubernetes/role/substrate-credential-provider |
  jq '.data | {bound_service_account_names, bound_service_account_namespaces, audience, token_policies, token_ttl}'
```

- `bound_service_account_names` and `bound_service_account_namespaces`: only `credprovider-openbao/openbao-credential-provider` may log in with this role.
- `audience: "openbao"`: the token must have been issued for OpenBao. The provider does not present the ServiceAccount token Kubernetes mounts for its own API; it presents a second, projected token whose audience is `openbao`. The Kubernetes API server will not accept that token as a credential, and OpenBao will not accept a token meant for anything else.
- `token_policies`: the OpenBao token the login returns carries the `substrate-credential-provider` policy.
- `token_ttl: 300`: that OpenBao token lasts five minutes. The provider caches it and logs in again before it expires.

The provider's projected token comes from a volume in its Deployment:

```shell
kubectl --context "$CTX" -n credprovider-openbao get deployment openbao-credential-provider -o json |
  jq '.spec.template.spec.volumes[] | select(.name == "openbao-token")'
```

Expect a `serviceAccountToken` with audience `openbao` and `expirationSeconds: 600`. The kubelet replaces the token before it expires, and the provider reads the file again at every login.

### What the login may read

OpenBao denies everything a policy does not grant. Secrets live in a secrets engine mounted at a path; `secret/` is a KV version 2 engine:

```shell
bao read -format=json sys/mounts/secret | jq -c '.data | {type, options}'
bao policy read substrate-credential-provider
```

Expect `{"type":"kv","options":{"version":"2"}}`. The policy grants `read` on `secret/data/atespaces/*`. KV version 2 keeps a secret's value under `data/` in its API path, so `bao kv get secret/atespaces/book-workbench/source-control` reads `secret/data/atespaces/book-workbench/source-control`; the CLI adds the `data/` segment for you. The `*` matches anything after the prefix, so the provider may read every Atespace's secrets.

That breadth is deliberate. OpenBao sees one login for every Actor, so it cannot tell them apart; the provider keeps each Actor to `secret/atespaces/<its Atespace>/` before it asks OpenBao, which section 4 shows.

### What the audit log records

An audit device logs every request and response as one JSON line. Setup enabled a `file` device that writes to OpenBao's stdout, so `kubectl logs` reads it:

```shell
bao audit list
bao read -format=json sys/config/auditing/request-headers | jq .data.headers
```

OpenBao leaves request headers out of the audit log unless you name them, and it hashes (HMACs) sensitive values in the log by default. Setup named `x-substrate-actor` with `hmac: false`, so the log shows it in clear text. The provider sends `X-Substrate-Actor: actor/<atespace>/<name>` on every read. OpenBao does not check the header; it records the provider's statement of whom the read is for.

### The secrets and the gateway

Setup configures OpenBao but stores no secrets. `prep-openbao-demo.sh` writes them on every run, because the development OpenBao keeps its data in memory. The account token is at the path for `book-workbench`:

```shell
bao kv get -field=token secret/atespaces/book-workbench/source-control; echo
```

It is the same account token the Kubernetes Secrets walkthrough injects from `workbench-secrets/git-token`, which `prep-openbao-demo.sh` copied into OpenBao. `team-a` holds a different token at its own path:

```shell
kubectl --context "$CTX" get secret git-token -n workbench-secrets -o jsonpath='{.data.token}' | base64 -d; echo
bao kv get -field=token secret/atespaces/team-a/source-control; echo
```

The gateway reaches each provider by its URI authority, next to `k8s.io`:

```shell
kubectl --context "$CTX" -n ate-system get configmap atenet-egress-agentgateway-config \
  -o jsonpath='{.data.config\.yaml}' | grep 'uriAuthority'
```

Expect `k8s.io`, `openbao` and `openbao-actor` twice each, once for the HTTPS listener and once for HTTP. The released chart renders only `k8s.io`, so setup patches this ConfigMap; any `helm upgrade` drops the patch and `prep-openbao-demo.sh` reapplies it (see `scripts/openbao-gateway-config.sh`).

## 2. Create and complete an Actor

As in the Kubernetes walkthrough:

```shell
kubectl ate --context "$CTX" create actor repo-workbench-1 -a book-workbench --template repo-workbench
kubectl ate --context "$CTX" resume actor repo-workbench-1 -a book-workbench
wb /api/actors/repo-workbench-1/fast-forward POST '{"expectedStep": 0}' | jq '{phase, canPublish}'
```

## 3. Inject the token from OpenBao

The rule names the secret by an `ate-secret://openbao/kv/<mount>/<path>/<field>` URI:

```shell
kubectl ate --context "$CTX" create egress-policy repo-workbench-1 -a book-workbench -f demo/policies/openbao/2-inject.yaml
publish repo-workbench-1
```

Expect `status: 201`, `sentHeaders: {"Authorization": "placeholder"}`, and `authenticatedAs: "repo-workbench-bot"`. If the first publish after the policy change returns the DNS error, publish again; the policy takes a few seconds to arrive.

OpenBao's audit log records the read:

```shell
kubectl --context "$CTX" logs -n openbao deployment/openbao --since=5m |
  jq -rR 'fromjson? | select(.type == "response" and ((.auth.display_name // "") | test("^(jwt|kubernetes)-")))
          | "\(.request.path)  \(.auth.display_name)  \(.request.headers["x-substrate-actor"][0] // "")  \(.error // "allowed" | gsub("\\s+"; " "))"'
```

Each line is one OpenBao response: the API path, the identity OpenBao authenticated, the Actor named in the provider's `X-Substrate-Actor` header (reads only), and `allowed` or OpenBao's error. The Kubernetes auth method names an identity `kubernetes-<namespace>-<serviceaccount>`; the JWT method names it `jwt-<the JWT's sub claim>`. The filter leaves out the `bao` commands run with the root token. The window is five minutes, so it also shows any earlier publish.

Expect a login, `auth/kubernetes/login  kubernetes-credprovider-openbao-openbao-credential-provider    allowed`, followed by `secret/data/atespaces/book-workbench/source-control  kubernetes-credprovider-openbao-openbao-credential-provider  actor/book-workbench/repo-workbench-1  allowed`. OpenBao authenticated the provider. The Actor's name is the provider's statement, which OpenBao records as given. The provider caches its OpenBao token for most of its five-minute TTL, so a publish soon after this one adds a read without a new login.

## 4. The provider's boundary

Point the same rule at another Atespace's token. The secret exists, the provider's OpenBao policy would allow the read, and the policy author may write any path into the URI:

```shell
kubectl ate --context "$CTX" delete egress-policy repo-workbench-1 -a book-workbench
kubectl ate --context "$CTX" create egress-policy repo-workbench-1 -a book-workbench -f demo/policies/openbao/3-other-atespace.yaml
publish repo-workbench-1
```

Expect `status: 403` and `actor egress policy denied`. The provider refused before asking OpenBao:

```shell
kubectl --context "$CTX" logs -n credprovider-openbao deployment/openbao-credential-provider --since=2m |
  jq -rR 'fromjson? | select(.msg | startswith("credential request denied")) | "\(.msg)  \(.actor)  \(.secret)"' | tail -1
```

Expect `credential request denied: secret is outside the actor's atespace  book-workbench/repo-workbench-1  secret/atespaces/team-a/source-control#token`. Rerun the audit query from section 3: OpenBao has no new entry. The gateway records the provider's answer:

```shell
kubectl --context "$CTX" logs -n ate-system deployment/atenet-egress --since=2m |
  grep 'http.status=403' | grep -o 'error=.* reason=[A-Za-z]*' | tail -1
```

Expect `credential provider openbao denied: ... actor book-workbench/repo-workbench-1 is not permitted to read secret/atespaces/team-a/source-control#token`, then `reason=Authorization`. A missing secret produces the same message, so an Actor cannot probe what exists.

### How the provider decides

The provider has no list of grants. It compares two strings from the `FetchSecret` request (`internal/provider/provider.go` in the plugin):

1. **The secret's Atespace.** It splits the URI `ate-secret://openbao/kv/secret/atespaces/team-a/source-control/token` into the mount `secret`, the path `atespaces/team-a/source-control`, and the field `token`. It rejects a URI with `.` or `..` segments, an empty segment, percent-encoding, a query, or a fragment, so the path cannot mean something other than what it shows.
2. **The Actor's Atespace.** It parses `actor_spiffe_id`, which must be `spiffe://substrate-actor.local/actor/<atespace>/<name>`, into `book-workbench` and `repo-workbench-1`.
3. **The comparison.** The path must begin `atespaces/<the Actor's Atespace>/`. Here it begins `atespaces/team-a/`, so the provider logs the refusal and returns `PermissionDenied` without logging in to OpenBao or reading anything.

Every refusal, missing secret, and missing field returns the same `PermissionDenied` message, and the gateway turns it into the `403`.

This rule rests on a few things:

- **A naming convention.** An Atespace's secrets must live under `secret/atespaces/<atespace>/`. The `openbao` provider cannot give a secret stored anywhere else to any Actor.
- **OpenBao's policy for the mount.** The provider checks the path, not the mount. A URI that names another mount passes its check, and OpenBao refuses the read, because the provider's policy grants only `secret/data/atespaces/*`. Together they keep each Actor to `secret/atespaces/<its Atespace>/`.
- **The gateway's word.** The Actor's Atespace comes from the `actor_spiffe_id` the gateway sends, as with every provider.
- **The provider itself.** Its OpenBao token can read every Atespace's secrets, so a provider that skipped this check could serve any of them.

The `openbao-actor` provider in section 5 skips this check. It reads with the Actor's own OpenBao token, and OpenBao's policy makes the same comparison.

## 5. The actor-jwt variant: OpenBao decides

The `openbao-actor` provider reads the same OpenBao, through its own login. Its identity may mint Actor JWTs, as section 1 showed, and OpenBao trusts Substrate's Actor JWT issuer and maps the JWT's `actor.ate.dev` claim into each entity's metadata:

```shell
bao read -format=json auth/jwt/config | jq '.data | {oidc_discovery_url, bound_issuer}'
bao read -format=json auth/jwt/role/substrate-actor | jq '.data | {user_claim, bound_audiences, claim_mappings, token_ttl}'
bao policy read substrate-actor
```

Expect the issuer `https://idp.ate-system.svc`, `user_claim: "sub"`, audience `openbao`, claim mappings for `atespace`, `actor`, and `actor_uid`, and one policy path: `secret/data/atespaces/{{identity.entity.aliases.<accessor>.metadata.atespace}}/*`. An Actor may read the secrets under its own Atespace and nothing else, and OpenBao enforces it.

Point the Actor's rule at the same token through `openbao-actor`:

```shell
kubectl ate --context "$CTX" delete egress-policy repo-workbench-1 -a book-workbench
kubectl ate --context "$CTX" create egress-policy repo-workbench-1 -a book-workbench -f demo/policies/openbao-actor/2-inject.yaml
publish repo-workbench-1
```

Expect `status: 201` again, and rerun the audit query from section 3. This time the login is `auth/jwt/login  jwt-actor/book-workbench/repo-workbench-1`, and the read is by that entity: OpenBao authenticated the Actor, not a broker account.

Then another Atespace's token:

```shell
kubectl ate --context "$CTX" delete egress-policy repo-workbench-1 -a book-workbench
kubectl ate --context "$CTX" create egress-policy repo-workbench-1 -a book-workbench -f demo/policies/openbao-actor/3-other-atespace.yaml
publish repo-workbench-1
kubectl --context "$CTX" logs -n openbao deployment/openbao --since=2m |
  jq -rR 'fromjson? | select(.type == "response" and .request.path == "secret/data/atespaces/team-a/source-control")
          | "\(.auth.display_name)  \(.error // "allowed" | gsub("\\s+"; " "))"' | tail -2
```

Expect `status: 403`, and OpenBao's audit log shows `jwt-actor/book-workbench/repo-workbench-1` with `permission denied`, twice. OpenBao refused this read itself, where in section 4 the provider refused and OpenBao saw nothing. The provider retries once with a fresh login when a cached token is refused, in case it was revoked; a refusal with a fresh token is final.

### What moved, and what did not

| Question | `k8s.io` | `openbao` | `openbao-actor` |
| --- | --- | --- | --- |
| Where does the credential live? | A Kubernetes Secret | OpenBao | OpenBao |
| Who decides which credential an Actor may read? | The provider, from a namespace grant loaded at startup | The provider, by Atespace path | OpenBao, from its policy, on every read |
| Who does the store authenticate? | The provider's ServiceAccount | The provider's ServiceAccount | The Actor, as entity `actor/<atespace>/<name>` |
| What does the store's audit log name? | Nothing per Actor | The Actor, as the provider states it | The Actor's own entity |
| Changing a grant | Helm value, provider restart | Provider change | `bao policy write`, no restart |
| What the provider holds in Substrate | Nothing | Nothing | The `egress_gateway` role: mints a JWT for any Actor, toward any audience |

Trust did not move. Every provider believes the gateway's claim about which Actor is asking, and OpenBao never sees proof from the Actor itself. `openbao-actor` moves the decision and the audit trail into OpenBao, at the cost of a Substrate role as broad as the gateway's. `openbao` keeps the provider's power inside OpenBao: compromised, it can read every Atespace's secrets there, and nothing in Substrate. The gateway's five-minute credential cache applies to both: a policy change reaches an Actor whose credential the gateway already cached only when that entry expires.

## 6. Cleanup

Delete the Actor (its egress policy goes with it). To remove the broker, restore the release to `substrate-values.yaml` and delete OpenBao and both providers:

```shell
kubectl ate --context "$CTX" delete actor repo-workbench-1 -a book-workbench
./scripts/teardown-openbao.sh
```

## Troubleshooting

- `403` **logged as "no credential provider configured" for** `openbao` **or** `openbao-actor`**.** A `helm upgrade` rendered the gateway config again without the provider. Rerun `./scripts/prep-openbao-demo.sh`.
- `500` or `403` **from** `openbao`**, logged by the provider as "OpenBao refused the provider's login".** OpenBao's Kubernetes auth role or its TokenReview permission is missing, typically after OpenBao restarted. Rerun `./scripts/prep-openbao-demo.sh`.
- `500` or `403` **from** `openbao-actor`**, logged as "not authorized to mint actor JWTs".** `ate-api-server` lacks the provider's `--egress-gateway-spiffe-id`; rerun `./scripts/setup-openbao.sh`.
- `503` **from the gateway.** OpenBao or a provider is unreachable; the gateway fails closed and the error is retryable. Check `kubectl --context "$CTX" get pods -n openbao`, `-n credprovider-openbao`, and `-n credprovider-openbao-actor`.
- **Every read denied after OpenBao restarted.** Development mode lost its configuration and data. Rerun `./scripts/prep-openbao-demo.sh`.
