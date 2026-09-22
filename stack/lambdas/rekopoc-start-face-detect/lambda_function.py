import hashlib
import json
import logging
import os
from urllib.parse import unquote_plus

import boto3
from rekognition import check_format_and_size, start_face_detection

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
reko = boto3.client("rekognition")
sfn = boto3.client("stepfunctions")


def lambda_handler(event, context):
    # S3 sends a test event when configuring notifications.
    if event.get("Event") == "s3:TestEvent":
        return {"statusCode": 200, "body": json.dumps("S3 notification configured")}

    successful_records = []
    failed_records = []
    for record in event["Records"]:
        bucket = key = None
        try:
            bucket = record["s3"]["bucket"]["name"]
            obj = record["s3"]["object"]
            key = unquote_plus(obj["key"])
            size = int(obj["size"])
            if not check_format_and_size(key, size):
                raise ValueError("Expected a nonempty MP4 or MOV smaller than 10 GiB")

            # S3 delivers at least once. A sequencer distinguishes overwrites of
            # the same key and makes retries of the same notification idempotent.
            identity = json.dumps(
                [bucket, key, obj.get("versionId"), obj.get("sequencer"), obj.get("eTag")],
                separators=(",", ":"),
            )
            token = hashlib.sha256(identity.encode()).hexdigest()
            job_id = start_face_detection(bucket, key, size, reko, token)
            input_data = json.dumps(
                {
                    "body": {
                        "job_id": job_id,
                        "s3_object_bucket": bucket,
                        "s3_object_key": key,
                    }
                }
            )
            try:
                sfn.start_execution(
                    stateMachineArn=os.environ["STATE_MACHINE_ARN"],
                    name=token,
                    input=input_data,
                )
            except sfn.exceptions.ExecutionAlreadyExists:
                logger.info("Workflow already started for this S3 notification")
            successful_records.append({"bucket": bucket, "key": key, "job_id": job_id})
        except Exception:
            logger.exception("Unable to start face detection")
            failed_records.append({"bucket": bucket, "key": key})

    if failed_records:
        # Raising lets Lambda retry the batch. Successful records are deduplicated.
        raise RuntimeError(f"Unable to process {len(failed_records)} S3 record(s)")
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "job_id": successful_records[-1]["job_id"] if successful_records else None,
                "successful_records": successful_records,
                "failed_records": [],
            }
        ),
    }
