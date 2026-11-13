# AWS: API on Lambda (container image + Function URL)

`template.yaml` (AWS SAM) deploys the FastAPI backend as one Lambda function in `ap-south-1`:

- image built from `backend/Dockerfile.lambda` (uses the [AWS Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter), so the app is plain uvicorn), face models baked in
- arm64 (Graviton), 1024 MB, 30 s timeout
- public **Function URL** (`AuthType: NONE`); the app itself rejects every request except `/api/health` unless it carries the `X-Origin-Auth` secret that only the Cloudflare proxy knows
- CloudWatch log group with 30-day retention
- EventBridge schedules that call the internal job endpoints (see "Scheduled jobs")
- secrets (`DatabaseUrl`, `DatabaseSystemUrl`, `DataKey`, `InternalJobSecret`, `JwtSecret`, `OriginSecret`) are `NoEcho` parameters stored as Lambda environment variables. They are visible to anyone with `lambda:GetFunctionConfiguration` on the account. If you outgrow that, move them to SSM Parameter Store / Secrets Manager.

## Prerequisites

- AWS account, an admin-capable CLI profile for the first deploy (`aws configure sso` or access keys)
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) and Docker (`sam build` builds the image; for arm64 on an x86 machine Docker needs QEMU, which Docker Desktop includes)
- A PostgreSQL 16 database behind a transaction pooler with the schema migrated (see "Database migrations")
- Secrets ready (see `docs/deploy.md`): app and system database URLs, `DATA_KEY`, internal job secret, JWT secret, origin secret

## First deploy

```bash
cd infra/aws
export AWS_REGION=ap-south-1

sam validate --lint
sam build                      # builds backend/Dockerfile.lambda for arm64
sam deploy --guided            # answers below
```

Guided prompts: stack name `attendify-api`, region `ap-south-1`, paste the six secrets, `AllowSignup` as you like, accept "Allow SAM CLI IAM role creation" (`CAPABILITY_IAM`), "Create managed ECR repositories" yes, "Lambda Function URL has no authentication, is this okay" yes (the shared secret protects it), save arguments to `samconfig.toml` yes. SAM never writes the secrets to `samconfig.toml` (they are `NoEcho`); the committed `samconfig.toml` holds only non-secret defaults.

Non-interactive equivalent (this is what CI runs):

```bash
sam deploy --no-confirm-changeset --no-fail-on-empty-changeset \
  --parameter-overrides DatabaseUrl="$DATABASE_URL" DatabaseSystemUrl="$DATABASE_SYSTEM_URL" \
    DataKey="$DATA_KEY" InternalJobSecret="$INTERNAL_JOB_SECRET" \
    JwtSecret="$JWT_SECRET" OriginSecret="$ORIGIN_SECRET"
```

Get the URL (used as `API_ORIGIN` in Cloudflare):

```bash
aws cloudformation describe-stacks --stack-name attendify-api --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='FunctionUrl'].OutputValue" --output text
```

Test it directly (expect 403 without the secret, 200 on health):

```bash
URL=https://xxxx.lambda-url.ap-south-1.on.aws
curl -i $URL/api/v1/health                                   # 200, health is exempt
curl -i $URL/api/v1/people/me                               # 403, no secret
curl -i -H "X-Origin-Auth: $ORIGIN_SECRET" $URL/api/v1/people/me   # 401, reached the app
```

If the URL returns `403 Forbidden` even on `/api/v1/health` with an `x-amzn-errortype` header (not our JSON body), the function's resource policy is missing; recent AWS accounts need both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` for public URLs. SAM adds the first automatically; if the second is missing run:

```bash
aws lambda add-permission --region ap-south-1 --function-name attendify-api-api \
  --statement-id FunctionURLInvokeAllowPublicAccess --action lambda:InvokeFunction \
  --principal "*" --invoked-via-function-url
```

## Cold starts and warm containers

The Web Adapter starts `uvicorn` once per execution environment, waits until `/api/health` answers (`AWS_LWA_READINESS_CHECK_PATH`, `/api/v1/health`), and then forwards each invocation to it as a normal HTTP request. The process, FastAPI lifespan and therefore the database engines (SQLAlchemy/asyncpg; `DB_NULL_POOL=true` so connections are not held across freezes) live as long as the container, and between invocations the container is frozen, not restarted, so warm requests reuse the client. Expect a cold start of roughly 3 to 6 s (Python + OpenCV import + first database connection) after idle periods; `AWS_LWA_ASYNC_INIT=true` lets that continue past Lambda's 10 s init cap. If cold starts matter, set provisioned concurrency to 1 (costs about 10 USD/month at 1024 MB) or ping `/api/health` every 5 minutes from an uptime monitor.

## Database migrations

The schema is owned by Alembic and applied as the **table owner** over a **direct** (non-pooled)
connection, never through the pooler and never as `attendify_app`:

```bash
cd backend
ALEMBIC_DATABASE_URL='postgresql://owner:PASSWORD@db-host:5432/postgres' uv run alembic upgrade head
```

CI does this before every deploy (the "Run database migrations" step) with the secret
`DATABASE_MIGRATION_URL`. Migrations `0001` (foundation) and `0002` (attendance) create the
`attendify_app` and `attendify_system` roles when the owner may; the optional
`ATTENDIFY_APP_PASSWORD` / `ATTENDIFY_SYSTEM_PASSWORD` secrets set their passwords on the run.
See `docs/deploy.md` for providers (Supabase, Neon, RDS) where a `BYPASSRLS` role must be created in
the dashboard first. Migrations are forward-only in CI; roll back by shipping a corrective
migration.

## Database network access

Lambda outside a VPC has no fixed outbound IP. Use a provider pooler endpoint with TLS and a strong
password for `attendify_app` and `attendify_system` (24+ random characters); the app role is
RLS-restricted and cannot see another organization's rows even if the application had a bug. For a
fixed egress IP put the function in private subnets behind a NAT gateway with an Elastic IP and
allow only that address at the database, or use the provider's private networking (RDS Proxy in
the same VPC, Supabase/Neon PrivateLink).

## Scheduled jobs

`template.yaml` creates an EventBridge **Connection** (API key header `X-Internal-Job-Secret`,
value `InternalJobSecret`), two **API destinations** and two **rules** that call the Function URL:

| Rule | Schedule | Endpoint |
|---|---|---|
| `attendify-api-close-stale-sessions` | every 5 minutes | `POST /api/v1/internal/jobs/close-stale-sessions` |
| `attendify-api-retention` | daily 21:30 UTC | `POST /api/v1/internal/jobs/retention` |

The job paths are exempt from the `X-Origin-Auth` check and are protected by the job secret alone
(constant-time compare; `503 jobs_disabled` when `INTERNAL_JOB_SECRET` is empty). The `recompute`
job (`POST /api/v1/internal/jobs/recompute` with `{start, end, org_id?, user_id?}`) is run by hand
after a policy or office change, for example:

```bash
curl -X POST "$URL/api/v1/internal/jobs/recompute" -H "X-Internal-Job-Secret: $INTERNAL_JOB_SECRET" \
  -H "Content-Type: application/json" -d '{"start":"2026-11-01","end":"2026-11-30"}'
```

Pause a schedule without redeploying: `aws events disable-rule --name attendify-api-retention`.

## Continuous deployment (GitHub Actions with OIDC)

`.github/workflows/deploy-backend.yml` assumes an IAM role through GitHub's OIDC provider, so no long-lived AWS keys are stored in GitHub. Do the first deploy by hand (above) with admin credentials: it creates the SAM-managed bucket and ECR repository stack, so the CI role does not need permission to create those.

1. Create the OIDC provider (once per account):

   ```bash
   aws iam create-open-id-connect-provider \
     --url https://token.actions.githubusercontent.com \
     --client-id-list sts.amazonaws.com
   ```

   (Since 2023 AWS validates GitHub's certificate itself; no thumbprint is required, though older CLIs may ask for `--thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1`.)

2. Create the role. Edit `github-oidc-trust-policy.json` (replace `<ACCOUNT_ID>`, `<GITHUB_OWNER>`, `<GITHUB_REPO>`; it only trusts the `main` branch), and `github-deploy-policy.json` (replace `<ACCOUNT_ID>`):

   ```bash
   aws iam create-role --role-name attendify-github-deploy \
     --assume-role-policy-document file://github-oidc-trust-policy.json
   aws iam put-role-policy --role-name attendify-github-deploy \
     --policy-name attendify-deploy --policy-document file://github-deploy-policy.json
   ```

3. In GitHub, Settings, Secrets and variables, Actions, add:
   - variable `AWS_DEPLOY_ROLE_ARN` = `arn:aws:iam::<ACCOUNT_ID>:role/attendify-github-deploy`
   - secrets `DATABASE_URL`, `DATABASE_SYSTEM_URL`, `DATABASE_MIGRATION_URL` (owner role, direct connection), `DATA_KEY`, `INTERNAL_JOB_SECRET`, `JWT_SECRET`, `ORIGIN_SECRET` (same values as the first deploy); optionally `ATTENDIFY_APP_PASSWORD` and `ATTENDIFY_SYSTEM_PASSWORD`

The policy is scoped to the `attendify-api` stack, `attendify-api-*` functions, roles, log groups, EventBridge rules/connection/API destinations and the connection's secret in `ap-south-1`. ECR access is limited to the account and region because SAM generates the repository name; tighten it to the real repository ARN after the first deploy (`aws ecr describe-repositories`). If a deploy fails with an `AccessDenied` for an action not listed, add that action; CloudFormation error text names it.

## Housekeeping

- Old images pile up in the managed ECR repository (about 0.1 USD/GB-month). Add a lifecycle rule: `aws ecr put-lifecycle-policy` keeping the last 5 images.
- Delete everything: `sam delete --stack-name attendify-api --region ap-south-1`.
