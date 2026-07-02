"""
upload_to_cos.py

Uploads ICS feeds and subscribe page to Tencent Cloud COS.
Run after ics_generator has written the feeds.

Required environment variables:
  TENCENT_SECRET_ID   - COS sub-user SecretId
  TENCENT_SECRET_KEY  - COS sub-user SecretKey
  TENCENT_BUCKET      - Bucket name, e.g. crslaisa-1449771562
  TENCENT_REGION      - Region, e.g. ap-guangzhou
"""

import os
import sys
import glob
from qcloud_cos import CosConfig, CosS3Client

def main():
    secret_id = os.environ.get("TENCENT_SECRET_ID")
    secret_key = os.environ.get("TENCENT_SECRET_KEY")
    bucket = os.environ.get("TENCENT_BUCKET")
    region = os.environ.get("TENCENT_REGION")

    if not all([secret_id, secret_key, bucket, region]):
        print("ERROR: Missing required environment variables.")
        sys.exit(1)

    config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key)
    client = CosS3Client(config)

    CONTENT_TYPES = {
        ".ics": "text/calendar; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }

    patterns = ["feeds/*.ics", "subscribe/index.html", "subscribe/*.jpg", "subscribe/*.png"]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))

    if not files:
        print("No files found to upload.")
        sys.exit(0)

    for local_path in files:
        key = local_path.replace("\\", "/")
        ext = os.path.splitext(local_path)[1].lower()
        content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
        print(f"Uploading {local_path} -> cos://{bucket}/{key}")
        with open(local_path, "rb") as f:
            client.put_object(
                Bucket=bucket,
                Body=f,
                Key=key,
                ContentType=content_type,
            )
        print(f"  OK: https://{bucket}.cos.{region}.myqcloud.com/{key}")

    print(f"Done. Uploaded {len(files)} file(s).")

if __name__ == "__main__":
    main()
