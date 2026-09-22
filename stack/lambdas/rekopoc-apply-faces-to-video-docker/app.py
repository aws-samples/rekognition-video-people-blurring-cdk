import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import boto3
from video_processor import apply_faces_to_video, integrate_audio

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
s3 = boto3.client("s3")


def lambda_function(event, context):
    bucket = event["s3_object_bucket"]
    key = event["s3_object_key"]
    suffix = Path(key).suffix.lower()
    if suffix not in {".mp4", ".mov"}:
        raise ValueError("Expected an MP4 or MOV video")

    # Keep compatibility with executions already carrying inline detection data.
    if "detections_s3_bucket" in event:
        result = s3.get_object(Bucket=event["detections_s3_bucket"], Key=event["detections_s3_key"])
        with result["Body"] as body:
            detections = json.load(body)
    else:
        detections = event
    timestamps = detections["timestamps"]
    metadata = detections["response"]["VideoMetadata"]

    # Never derive local paths from object keys or share files between invocations.
    # An exception exits before upload and is reported as a failed workflow task.
    with TemporaryDirectory(prefix="face-blur-", dir="/tmp") as directory:
        source = str(Path(directory) / f"input{suffix}")
        intermediate = str(Path(directory) / "blurred.avi")
        output = str(Path(directory) / f"output{suffix}")
        s3.download_file(bucket, key, source)
        frame_count = apply_faces_to_video(timestamps, source, intermediate, metadata)
        integrate_audio(source, intermediate, output, expected_frames=frame_count)
        s3.upload_file(
            output,
            os.environ["OUTPUT_BUCKET"],
            key,
            ExtraArgs={"ContentType": "video/quicktime" if suffix == ".mov" else "video/mp4"},
        )
    logger.info("Uploaded blurred video")
    return {"statusCode": 200, "body": json.dumps("Faces in video blurred")}
