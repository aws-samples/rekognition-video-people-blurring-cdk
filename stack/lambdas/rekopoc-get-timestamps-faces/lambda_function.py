import json
import os

import boto3

reko = boto3.client("rekognition")
s3 = boto3.client("s3")


def get_timestamps_and_faces(job_id, reko_client=None):
    if reko_client is None:
        reko_client = reko
    timestamps = {}
    metadata = None
    request = {"JobId": job_id, "MaxResults": 1000}
    while True:
        response = reko_client.get_face_detection(**request)
        if response["JobStatus"] != "SUCCEEDED":
            raise RuntimeError(f"Face detection did not succeed: {response['JobStatus']}")
        if metadata is None:
            metadata = response["VideoMetadata"]
        for face in response["Faces"]:
            timestamps.setdefault(str(face["Timestamp"]), []).append(face["Face"]["BoundingBox"])
        if not response.get("NextToken"):
            break
        request["NextToken"] = response["NextToken"]
    # Do not duplicate the final page of Faces or response headers.
    return timestamps, {"VideoMetadata": metadata}


def lambda_handler(event, context):
    job_id = event["job_id"]
    timestamps, response = get_timestamps_and_faces(job_id)
    detection_bucket = os.environ["DETECTION_BUCKET"]
    detection_key = f"detections/{job_id}.json"
    s3.put_object(
        Bucket=detection_bucket,
        Key=detection_key,
        Body=json.dumps({"timestamps": timestamps, "response": response}).encode(),
        ContentType="application/json",
    )
    return {
        "statusCode": 200,
        "body": {
            "job_id": job_id,
            "s3_object_bucket": event["s3_object_bucket"],
            "s3_object_key": event["s3_object_key"],
            "detections_s3_bucket": detection_bucket,
            "detections_s3_key": detection_key,
        },
    }
