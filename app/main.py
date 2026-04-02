import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import boto3
import yaml
from botocore.exceptions import ClientError
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks"
TESTCASE_TIMEOUT_MINUTES = 40

app = FastAPI(title="Result Storage")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.parsed_cors_allowed_origins,
    allow_origin_regex=settings.parsed_cors_allow_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _create_s3_client():
    client_kwargs = {
        "service_name": "s3",
        "region_name": settings.s3_region,
        "endpoint_url": settings.s3_endpoint_url,
    }
    if settings.use_static_s3_credentials:
        client_kwargs["aws_access_key_id"] = settings.s3_access_key
        client_kwargs["aws_secret_access_key"] = settings.s3_secret_key
    return boto3.client(**client_kwargs)


def _uses_aws_s3_endpoint() -> bool:
    endpoint = settings.s3_endpoint_url.rstrip("/")
    return endpoint == "https://s3.amazonaws.com" or endpoint.endswith(".amazonaws.com")


def _is_missing_bucket_error(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = str(error.get("Code", ""))
    return status_code == 404 or code in {"404", "NoSuchBucket", "NotFound"}


def _create_bucket() -> None:
    create_kwargs = {"Bucket": settings.s3_bucket_name}
    if _uses_aws_s3_endpoint() and settings.s3_region != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {
            "LocationConstraint": settings.s3_region
        }
    s3_client.create_bucket(**create_kwargs)


s3_client = _create_s3_client()


@app.on_event("startup")
def ensure_bucket():
    try:
        s3_client.head_bucket(Bucket=settings.s3_bucket_name)
    except ClientError as exc:
        if not _is_missing_bucket_error(exc):
            raise
        if not settings.s3_create_bucket_if_missing:
            raise RuntimeError(
                f"S3 bucket '{settings.s3_bucket_name}' is missing. "
                "Create it ahead of time or set S3_CREATE_BUCKET_IF_MISSING=true."
            ) from exc
        _create_bucket()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/app-name")
def get_app_name(auth_code: str):
    code_map = settings.auth_code_to_app
    if auth_code not in code_map:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"app_name": code_map[auth_code]}


@app.get("/app-url")
def get_app_url(auth_code: str):
    url_map = settings.auth_code_to_url
    if auth_code not in url_map:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"app_url": url_map[auth_code]}


def _attempt_key(app_name: str, auth_code: str, number: int) -> str:
    return f"{app_name}/{auth_code}/attempts/{number}.json"


def _active_key(app_name: str, auth_code: str) -> str:
    return f"{app_name}/{auth_code}/active.json"


def _get_s3_json(key: str) -> dict | None:
    try:
        obj = s3_client.get_object(Bucket=settings.s3_bucket_name, Key=key)
        return json.loads(obj["Body"].read())
    except ClientError:
        return None


def _check_no_active_testcase(app_name: str, auth_code: str) -> None:
    active = _get_s3_json(_active_key(app_name, auth_code))
    if active is None:
        return
    started_at = datetime.fromisoformat(active["started_at"])
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    if elapsed <= TESTCASE_TIMEOUT_MINUTES * 60:
        raise HTTPException(
            status_code=409,
            detail=f"Testcase {active['number']} is already in progress",
        )


@app.get("/testcase")
def get_testcase(auth_code: str, number: int):
    code_map = settings.auth_code_to_app
    if auth_code not in code_map:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not 1 <= number <= 20:
        raise HTTPException(status_code=400, detail="Number must be between 1 and 20")

    app_name = code_map[auth_code]

    attempt = _get_s3_json(_attempt_key(app_name, auth_code, number))
    if attempt is not None:
        raise HTTPException(status_code=403, detail="Testcase already started")

    _check_no_active_testcase(app_name, auth_code)

    path = TASKS_DIR / f"{app_name}.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Testcase not found")

    with open(path) as f:
        data = yaml.safe_load(f)

    cases = data.get("test_cases", [])
    if number > len(cases):
        raise HTTPException(status_code=404, detail="Testcase not found")

    started_at = datetime.now(timezone.utc).isoformat()
    meta = {"number": number, "started_at": started_at}
    s3_client.put_object(
        Bucket=settings.s3_bucket_name,
        Key=_attempt_key(app_name, auth_code, number),
        Body=json.dumps(meta),
        ContentType="application/json",
    )
    s3_client.put_object(
        Bucket=settings.s3_bucket_name,
        Key=_active_key(app_name, auth_code),
        Body=json.dumps(meta),
        ContentType="application/json",
    )

    return {"app_name": app_name, "number": number, "prompt": cases[number - 1]["prompt"], "started_at": started_at}


@app.post("/upload")
def upload(
    file: UploadFile = File(...),
    auth_code: str = Form(...),
    number: int = Form(...),
):
    code_map = settings.auth_code_to_app
    if auth_code not in code_map:
        raise HTTPException(status_code=401, detail="Unauthorized")

    app_name = code_map[auth_code]

    attempt = _get_s3_json(_attempt_key(app_name, auth_code, number))
    if attempt is None:
        raise HTTPException(status_code=403, detail="Testcase not started")

    started_at = datetime.fromisoformat(attempt["started_at"])
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    if elapsed > TESTCASE_TIMEOUT_MINUTES * 60:
        raise HTTPException(status_code=403, detail="Testcase time limit exceeded")

    filename = f"{uuid4().hex}_{file.filename or 'upload'}"
    key = f"{app_name}/{number}/{auth_code}/{filename}"
    s3_client.upload_fileobj(file.file, settings.s3_bucket_name, key)

    s3_client.delete_object(
        Bucket=settings.s3_bucket_name,
        Key=_active_key(app_name, auth_code),
    )

    return {"status": "ok", "key": key}
