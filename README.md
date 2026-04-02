# Result Storage

Small FastAPI service for:

- validating auth codes against a configured app map
- serving testcase prompts from the local `tasks/` directory
- storing testcase state and uploaded ZIP files in S3

## Runtime configuration

Required environment variables:

- `S3_BUCKET_NAME`
- `AUTH_CODE_MAP`

Optional environment variables:

- `S3_REGION` default `us-east-1`
- `S3_ENDPOINT_URL` default `https://s3.amazonaws.com`
- `S3_ACCESS_KEY` and `S3_SECRET_KEY` for local dev or non-AWS S3-compatible storage
- `S3_CREATE_BUCKET_IF_MISSING` default `false`
- `CORS_ALLOWED_ORIGINS` comma-separated list, default `http://localhost:3000`
- `CORS_ALLOW_ORIGIN_REGEX` regex, default `https://.*\.vercel\.app`

`AUTH_CODE_MAP` format:

```text
AUTH_CODE:APP_NAME:APP_URL,AUTH_CODE:APP_NAME:APP_URL
```

Example:

```text
ABC12345:circuit:https://circuit.example.com,XYZ98765:flightradar:https://flightradar.example.com
```

Default CORS behavior allows browser requests from:

- `http://localhost:3000`
- any `https://*.vercel.app` deployment URL

If you use a custom production domain, add it to `CORS_ALLOWED_ORIGINS`.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

If you use AWS S3 in production, leave `S3_ACCESS_KEY` and `S3_SECRET_KEY` unset and let boto3 use the instance role credentials provided by AWS.

## AWS deployment

Recommended target: AWS App Runner.

Why App Runner fits this app:

- the service is a single stateless HTTP API
- it already exposes a simple health endpoint at `/health`
- it stores state and uploads in S3 instead of on local disk

### One-time AWS setup

1. Create an S3 bucket in the same region as the service.
2. Create an IAM policy for S3 access and attach it to an App Runner instance role. A starter policy is in [aws/iam/apprunner-s3-policy.json](/home/mvysot/Desktop/agent-benchmark-result-server-main/aws/iam/apprunner-s3-policy.json).
3. Create the App Runner service and attach that instance role so the app can call S3 without static keys.
4. Configure runtime variables:
   - `S3_BUCKET_NAME`
   - `S3_REGION`
   - `S3_ENDPOINT_URL=https://s3.amazonaws.com`
   - `S3_CREATE_BUCKET_IF_MISSING=false`
   - `AUTH_CODE_MAP`

For production, treat `AUTH_CODE_MAP` as sensitive because it contains the auth codes. App Runner supports reading secrets and parameters into environment variables, so prefer AWS Systems Manager Parameter Store or AWS Secrets Manager over plain text service config.

### App Runner from source code

Use this path if the repository is in GitHub or Bitbucket and you want App Runner to build from source.

1. Push this repository to GitHub or Bitbucket.
2. In App Runner, create a service from a source repository.
3. Point the service at the repository root so App Runner picks up `apprunner.yaml`.
4. Set the health check path to `/health`.
5. Attach the instance role with S3 permissions.
6. Deploy.

The checked-in `apprunner.yaml` is intentionally set up for App Runner's Python 3.11 managed runtime. It installs dependencies in `run.pre-run`, which matches AWS's current guidance for the revised Python 3.11 build process.

### App Runner from ECR

Use this path if you want to build locally or in CI and deploy a container image.

1. Build the image:

```bash
docker build -t result-storage:latest .
```

2. Push the image to Amazon ECR.
3. Create an App Runner service from that ECR image.
4. Attach the App Runner access role for ECR if the repository is private.
5. Attach the same App Runner instance role for S3 access.
6. Set the same runtime variables as above and set the health check path to `/health`.

`apprunner.yaml` is not used for image-based App Runner services. For the ECR path, configure the port, health check, environment variables, and secrets in the App Runner service settings.

## Notes

- `Dockerfile` now copies the `tasks/` directory. Without that, `/testcase` fails in container-based deployments.
- The app no longer requires static S3 keys. On AWS it will use the App Runner instance role automatically.
- Bucket auto-creation is off by default because production buckets should be created and permissioned outside the app.
