from pathlib import PurePosixPath

import boto3


def check_format_and_size(filename, size):
    return PurePosixPath(filename).suffix.lower() in {".mp4", ".mov"} and 0 < size < 10 * 1024**3


def start_face_detection(bucket, video, size, reko_client=None, client_request_token=None):
    if not check_format_and_size(video, size):
        raise ValueError("Rekognition requires a nonempty MP4 or MOV smaller than 10 GiB")
    if reko_client is None:
        reko_client = boto3.client("rekognition")
    request = {"Video": {"S3Object": {"Bucket": bucket, "Name": video}}}
    if client_request_token:
        request["ClientRequestToken"] = client_request_token
    return reko_client.start_face_detection(**request)["JobId"]
