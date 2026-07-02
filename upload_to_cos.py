"""
upload_to_cos.py

Uploads all ICS files from the feeds/ directory to Tencent Cloud COS.
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

    ics_files = glob.glob("feeds/*.ics")
    if not ics_files:
        print("No ICS files found in feeds/")
        sys.exit(0)

    for local_path in ics_files:
        key = local_path.replace("\\", "/")
        print(f"Uploading {local_path} -> cos://{bucket}/{key}")
        with open(local_path, "rb") as f:
            client.put_object(
                Bucket=bucket,
                Body=f,
                Key=key,
                ContentType="text/calendar; charset=utf-8",
            )
        print(f"  OK: https://{bucket}.cos.{region}.myqcloud.com/{key}")

    print(f"Done. Uploaded {len(ics_files)} file(s).")

if __name__ == "__main__":
    main()
