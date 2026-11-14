"""Object storage. Local disk for development, any S3-compatible bucket (Cloudflare R2) in production."""
from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path

from .config import BACKEND_ROOT


class Storage:
    def put_file(self, local: Path, key: str) -> str: ...
    def get_file(self, key: str, local: Path) -> Path: ...
    def url(self, key: str | None) -> str | None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage(Storage):
    def __init__(self, root: Path, public_base: str = ""):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base = public_base.rstrip("/")

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("invalid storage key")
        return p

    def put_file(self, local: Path, key: str) -> str:
        dst = self._p(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if Path(local).resolve() != dst:
            shutil.copyfile(local, dst)
        return key

    def get_file(self, key: str, local: Path) -> Path:
        local.parent.mkdir(parents=True, exist_ok=True)
        src = self._p(key)
        if src != Path(local).resolve():
            shutil.copyfile(src, local)
        return local

    def url(self, key: str | None) -> str | None:
        return f"{self.public_base}/files/{key}" if key else None

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def path(self, key: str) -> Path:
        return self._p(key)


class S3Storage(Storage):
    """Cloudflare R2 / AWS S3 / MinIO. Files stay private; the browser gets short-lived signed URLs."""

    def __init__(self, bucket: str, endpoint: str, key_id: str, secret: str, region: str = "auto", url_ttl: int = 3600):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.ttl = url_ttl
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            region_name=region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put_file(self, local: Path, key: str) -> str:
        ctype = mimetypes.guess_type(str(local))[0] or "application/octet-stream"
        self.s3.upload_file(str(local), self.bucket, key, ExtraArgs={"ContentType": ctype})
        return key

    def get_file(self, key: str, local: Path) -> Path:
        local.parent.mkdir(parents=True, exist_ok=True)
        self.s3.download_file(self.bucket, key, str(local))
        return local

    def url(self, key: str | None) -> str | None:
        if not key:
            return None
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.ttl)

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        from .config import _env

        if _env("S3_BUCKET"):
            _storage = S3Storage(
                bucket=_env("S3_BUCKET"),
                endpoint=_env("S3_ENDPOINT"),
                key_id=_env("S3_ACCESS_KEY_ID"),
                secret=_env("S3_SECRET_ACCESS_KEY"),
                region=_env("S3_REGION", "auto"),
            )
        else:
            _storage = LocalStorage(
                Path(_env("GD_STORAGE_DIR", str(BACKEND_ROOT / ".storage"))),
                public_base=_env("PUBLIC_API_URL"),
            )
    return _storage


def set_storage(s: Storage) -> None:
    global _storage
    _storage = s
