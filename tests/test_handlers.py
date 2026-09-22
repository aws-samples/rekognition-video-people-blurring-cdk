import io
import json
from pathlib import Path
from unittest.mock import Mock

import boto3
import pytest
from botocore.stub import Stubber


def record(key="folder/a+video.mp4", sequencer="1", size=100):
    return {
        "s3": {
            "bucket": {"name": "input-bucket"},
            "object": {
                "key": key,
                "size": size,
                "sequencer": sequencer,
                "eTag": "etag",
            },
        }
    }


@pytest.fixture
def start(load_lambda, monkeypatch):
    module = load_lambda("rekopoc-start-face-detect")
    monkeypatch.setenv(
        "STATE_MACHINE_ARN", "arn:aws:states:us-east-1:123456789012:stateMachine:test"
    )
    module.reko = Mock()
    module.reko.start_face_detection.return_value = {"JobId": "job-id"}
    client = boto3.client("stepfunctions")
    module.sfn = Mock()
    module.sfn.exceptions = client.exceptions
    return module


def test_starts_every_record_and_decodes_keys(start):
    response = start.lambda_handler({"Records": [record(), record("b.mov", "2")]}, None)
    assert len(json.loads(response["body"])["successful_records"]) == 2
    requests = start.sfn.start_execution.call_args_list
    assert len(requests) == 2
    assert json.loads(requests[0].kwargs["input"])["body"]["s3_object_key"] == "folder/a video.mp4"
    assert requests[0].kwargs["name"] != requests[1].kwargs["name"]


def test_notification_retry_is_idempotent_and_overwrite_is_distinct(start):
    start.lambda_handler({"Records": [record()]}, None)
    first = start.sfn.start_execution.call_args.kwargs
    start.sfn.start_execution.side_effect = start.sfn.exceptions.ExecutionAlreadyExists(
        {"Error": {"Code": "ExecutionAlreadyExists", "Message": "exists"}}, "StartExecution"
    )
    start.lambda_handler({"Records": [record()]}, None)
    assert start.sfn.start_execution.call_args.kwargs == first
    start.sfn.start_execution.side_effect = None
    start.lambda_handler({"Records": [record(sequencer="2")]}, None)
    assert start.sfn.start_execution.call_args.kwargs["name"] != first["name"]


@pytest.mark.parametrize("bad", [record(size=0), record(size=10 * 1024**3), record("a.txt"), {}])
def test_invalid_records_fail_without_losing_other_records(start, bad):
    with pytest.raises(RuntimeError, match="1 S3 record"):
        start.lambda_handler({"Records": [bad, record()]}, None)
    start.sfn.start_execution.assert_called_once()


def test_start_failure_is_retriable(start):
    start.sfn.start_execution.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        start.lambda_handler({"Records": [record()]}, None)


def test_s3_test_event(start):
    assert start.lambda_handler({"Event": "s3:TestEvent"}, None)["statusCode"] == 200
    start.reko.start_face_detection.assert_not_called()


def test_start_helper_rejects_invalid_size(load_lambda):
    module = load_lambda("rekopoc-start-face-detect", "rekognition.py")
    with pytest.raises(ValueError):
        module.start_face_detection("bucket", "a.mp4", 0)


def test_status_request_and_context(load_lambda):
    module = load_lambda("rekopoc-check-status")
    event = {"job_id": "job", "s3_object_bucket": "bucket", "s3_object_key": "key"}
    with Stubber(module.reko) as stub:
        stub.add_response(
            "get_face_detection", {"JobStatus": "FAILED"}, {"JobId": "job", "MaxResults": 1}
        )
        assert module.lambda_handler(event, None)["body"] == {**event, "job_status": "FAILED"}
        stub.assert_no_pending_responses()


def test_paginated_detection_results(load_lambda):
    module = load_lambda("rekopoc-get-timestamps-faces")
    box = {"Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.4}
    metadata = {"FrameRate": 29.97, "FrameWidth": 160, "FrameHeight": 120}
    page = {
        "JobStatus": "SUCCEEDED",
        "VideoMetadata": metadata,
        "Faces": [{"Timestamp": 0, "Face": {"BoundingBox": box}}],
    }
    with Stubber(module.reko) as stub:
        stub.add_response(
            "get_face_detection",
            {**page, "NextToken": "next"},
            {"JobId": "job", "MaxResults": 1000},
        )
        stub.add_response(
            "get_face_detection", page, {"JobId": "job", "MaxResults": 1000, "NextToken": "next"}
        )
        timestamps, response = module.get_timestamps_and_faces("job")
        assert timestamps == {"0": [box, box]}
        assert response == {"VideoMetadata": metadata}
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("status", ["FAILED", "IN_PROGRESS"])
def test_unsuccessful_detection_not_published(load_lambda, status):
    module = load_lambda("rekopoc-get-timestamps-faces")
    with Stubber(module.reko) as stub:
        stub.add_response(
            "get_face_detection", {"JobStatus": status}, {"JobId": "job", "MaxResults": 1000}
        )
        with pytest.raises(RuntimeError):
            module.get_timestamps_and_faces("job")


def test_large_payload_is_stored_in_s3(load_lambda, monkeypatch):
    module = load_lambda("rekopoc-get-timestamps-faces")
    monkeypatch.setenv("DETECTION_BUCKET", "detections")
    payload = {str(i): [{"Left": 0, "Top": 0, "Width": 1, "Height": 1}] for i in range(5000)}
    module.get_timestamps_and_faces = Mock(return_value=(payload, {"VideoMetadata": {}}))
    module.s3 = Mock()
    event = {"job_id": "job", "s3_object_bucket": "bucket", "s3_object_key": "key"}
    result = module.lambda_handler(event, None)
    assert len(json.dumps(result)) < 1024
    saved = module.s3.put_object.call_args.kwargs
    assert len(saved["Body"]) > 256 * 1024
    assert json.loads(saved["Body"])["timestamps"] == payload
    assert result["body"]["detections_s3_key"] == saved["Key"]


@pytest.fixture
def blur(load_lambda, monkeypatch):
    module = load_lambda("rekopoc-apply-faces-to-video-docker", "app.py")
    monkeypatch.setenv("OUTPUT_BUCKET", "output-bucket")
    module.s3 = Mock()
    module.apply_faces_to_video = Mock()
    module.integrate_audio = Mock()
    return module


def blur_event():
    return {
        "s3_object_bucket": "input-bucket",
        "s3_object_key": "folder/a.mp4",
        "timestamps": {},
        "response": {"VideoMetadata": {}},
    }


@pytest.mark.parametrize("stage", ["download", "blur", "encode", "upload"])
def test_processing_failures_propagate_and_cleanup(blur, stage):
    failing = {
        "download": blur.s3.download_file,
        "blur": blur.apply_faces_to_video,
        "encode": blur.integrate_audio,
        "upload": blur.s3.upload_file,
    }[stage]
    failing.side_effect = RuntimeError(stage)
    with pytest.raises(RuntimeError, match=stage):
        blur.lambda_function(blur_event(), None)
    if stage != "upload":
        blur.s3.upload_file.assert_not_called()
    directory = Path(blur.s3.download_file.call_args.args[2]).parent
    assert not directory.exists()


def test_loads_s3_detections_and_preserves_output_key(blur):
    event = blur_event()
    payload = {k: event.pop(k) for k in ["timestamps", "response"]}
    body = io.BytesIO(json.dumps(payload).encode())
    blur.s3.get_object.return_value = {"Body": body}
    event.update(detections_s3_bucket="detections", detections_s3_key="detections/job.json")
    blur.lambda_function(event, None)
    assert body.closed
    args = blur.s3.upload_file.call_args
    assert args.args[1:] == ("output-bucket", "folder/a.mp4")
    assert args.kwargs["ExtraArgs"] == {"ContentType": "video/mp4"}
    assert not Path(args.args[0]).parent.exists()


def test_missing_detection_data_prevents_download(blur):
    with pytest.raises(KeyError):
        blur.lambda_function({"s3_object_bucket": "bucket", "s3_object_key": "a.mp4"}, None)
    blur.s3.download_file.assert_not_called()
