# AWS: API on Lambda (container image + Function URL)

`template.yaml` (AWS SAM) deploys the FastAPI backend as one Lambda function in `ap-south-1`:

- image built from `backend/Dockerfile.lambda` (uses the [AWS Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter), so the app is plain uvicorn), face models baked in
- arm64 (Graviton), 1024 MB, 30 s timeout
- public **Function URL** (`AuthType: NONE`); the app itself rejects every request except `/api/health` unless it carries the `X-Origin-Auth` secret that only the Cloudflare proxy knows
- CloudWatch log group with 30-day retention
- secrets (`MongoUri`, `JwtSecret`, `OriginSecret`) are `NoEcho` parameters stored as Lambda environment variables. They are visible to anyone with `lambda:GetFunctionConfiguration` on the account. If you outgrow that, move them to SSM Parameter Store / Secrets Manager.

## Prerequisites

- AWS account, an admin-capable CLI profile for the first deploy (`aws configure sso` or access keys)
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) and Docker (`sam build` builds the image; for arm64 on an x86 machine Docker needs QEMU, which Docker Desktop includes)
- Three secrets ready (see `docs/deploy.md` step 2): Atlas connection string, JWT secret, origin secret

## First deploy

```bash
cd infra/aws
export AWS_REGION=ap-south-1

sam validate --lint
sam build                      # builds backend/Dockerfile.lambda for arm64
sam deploy --guided            # answers below
```

Guided prompts: stack name `attendify-api`, region `ap-south-1`, paste the three secrets, `AllowSelfRegistration` as you like, accept "Allow SAM CLI IAM role creation" (`CAPABILITY_IAM`), "Create managed ECR repositories" yes, "Lambda Function URL has no authentication, is this okay" yes (the shared secret protects it), save arguments to `samconfig.toml` yes. SAM never writes the secrets to `samconfig.toml` (they are `NoEcho`); the committed `samconfig.toml` holds only non-secret defaults.

Non-interactive equivalent (this is what CI runs):

```bash
sam deploy --no-confirm-changeset --no-fail-on-empty-changeset \
  --parameter-overrides MongoUri="$MONGODB_URI" JwtSecret="$JWT_SECRET" OriginSecret="$ORIGIN_SECRET"
```

Get the URL (used as `API_ORIGIN` in Cloudflare):

```bash
aws cloudformation describe-stacks --stack-name attendify-api --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='FunctionUrl'].OutputValue" --output text
```

Test it directly (expect 403 without the secret, 200 on health):

```bash
URL=https://xxxx.lambda-url.ap-south-1.on.aws
curl -i $URL/api/health                                   # 200, health is exempt
curl -i $URL/api/auth/me                                  # 403, no secret
curl -i -H "X-Origin-Auth: $ORIGIN_SECRET" $URL/api/auth/me   # 401, reached the app
```

If the URL returns `403 Forbidden` even on `/api/health` with an `x-amzn-errortype` header (not our JSON body), the function's resource policy is missing; recent AWS accounts need both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` for public URLs. SAM adds the first automatically; if the second is missing run:

```bash
aws lambda add-permission --region ap-south-1 --function-name attendify-api-api \
  --statement-id FunctionURLInvokeAllowPublicAccess --action lambda:InvokeFunction \
  --principal "*" --invoked-via-function-url
```

## Cold starts and warm containers

The Web Adapter starts `uvicorn` once per execution environment, waits until `/api/health` answers (`AWS_LWA_READINESS_CHECK_PATH`), and then forwards each invocation to it as a normal HTTP request. The process, FastAPI lifespan and therefore the single `AsyncMongoClient` (and its connection pool) live as long as the container, and between invocations the container is frozen, not restarted, so warm requests reuse the client. Expect a cold start of roughly 3 to 6 s (Python + OpenCV import + Mongo connect + index check) after idle periods; `AWS_LWA_ASYNC_INIT=true` lets that continue past Lambda's 10 s init cap. If cold starts matter, set provisioned concurrency to 1 (costs about 10 USD/month at 1024 MB) or ping `/api/health` every 5 minutes from an uptime monitor.

## MongoDB Atlas network access

Lambda outside a VPC has no fixed outbound IP, so the Atlas IP access list cannot be narrowed to one address.

- **Default (free): allow `0.0.0.0/0`** in Atlas, Network Access, and rely on a strong database user password (24+ random characters), a database user limited to the `attendify` database (`readWrite` role only, not Atlas admin), TLS (always on for Atlas), and no other databases on the cluster. Rotate the password if it ever leaks.
- **Paid option: VPC + NAT gateway with an Elastic IP.** Put the function in private subnets, route outbound through a NAT gateway (about 35 USD/month plus data in `ap-south-1`) and allow only the Elastic IP in Atlas. Requires `VpcConfig` in `template.yaml`.
- **Paid option: PrivateLink** (Atlas M10+). Private endpoint in your VPC, Lambda in that VPC. Also needs VPC networking; interface endpoint costs about 7 to 10 USD/month per AZ.

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
   - secrets `MONGODB_URI`, `JWT_SECRET`, `ORIGIN_SECRET` (same values as the first deploy)

The policy is scoped to the `attendify-api` stack, `attendify-api-*` functions, roles and log groups in `ap-south-1`. ECR access is limited to the account and region because SAM generates the repository name; tighten it to the real repository ARN after the first deploy (`aws ecr describe-repositories`). If a deploy fails with an `AccessDenied` for an action not listed, add that action; CloudFormation error text names it.

## Housekeeping

- Old images pile up in the managed ECR repository (about 0.1 USD/GB-month). Add a lifecycle rule: `aws ecr put-lifecycle-policy` keeping the last 5 images.
- Delete everything: `sam delete --stack-name attendify-api --region ap-south-1`.
