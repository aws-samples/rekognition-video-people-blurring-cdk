"""Exercise the Lambda handler and real codecs using generated MP4/MOV inputs.

Runs locally or inside the built Lambda image; S3 alone is replaced with local
files, so no AWS account, network connection, or external sample video is needed.
"""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
os.environ["OUTPUT_BUCKET"] = "output"
sys.path.insert(
    0,
    os.environ.get(
        "LAMBDA_TASK_ROOT",
        str(
            Path(__file__).resolve().parents[1]
            / "stack/lambdas/rekopoc-apply-faces-to-video-docker"
        ),
    ),
)

import cv2  # noqa: E402
import imageio_ffmpeg  # noqa: E402
import numpy as np  # noqa: E402

import app  # noqa: E402


def run_case(directory, suffix, audio, faces=True):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = directory / f"source{suffix}"
    result = directory / f"result{suffix}"
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x120:rate=30000/1001",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    command += ["-t", "1.001", "-frames:v", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac"]
    subprocess.run([*command, str(source)], check=True, timeout=30)
    payload = {
        "timestamps": {
            str(t): [{"Left": -0.1, "Top": -0.1, "Width": 1.2, "Height": 1.2}] for t in (0, 500)
        },
        "response": {
            "VideoMetadata": {
                "FrameRate": 30000 / 1001,
                "FrameWidth": 160,
                "FrameHeight": 120,
            }
        },
    }
    if not faces:
        payload["timestamps"] = {}
    downloaded_paths = []

    class LocalS3:
        def get_object(self, **kwargs):
            assert kwargs == {"Bucket": "detections", "Key": "detections/job.json"}
            return {"Body": io.BytesIO(json.dumps(payload).encode())}

        def download_file(self, bucket, key, filename):
            assert bucket == "input" and key == f"nested/video{suffix}"
            downloaded_paths.append(Path(filename))
            shutil.copyfile(source, filename)

        def upload_file(self, filename, bucket, key, ExtraArgs):
            assert bucket == "output" and key == f"nested/video{suffix}"
            assert ExtraArgs["ContentType"] == (
                "video/mp4" if suffix == ".mp4" else "video/quicktime"
            )
            shutil.copyfile(filename, result)

    app.s3 = LocalS3()
    response = app.lambda_function(
        {
            "s3_object_bucket": "input",
            "s3_object_key": f"nested/video{suffix}",
            "detections_s3_bucket": "detections",
            "detections_s3_key": "detections/job.json",
        },
        None,
    )
    assert response["statusCode"] == 200
    assert all(not path.parent.exists() for path in downloaded_paths)

    capture = cv2.VideoCapture(str(result))
    assert capture.isOpened()
    assert abs(capture.get(cv2.CAP_PROP_FPS) - 30000 / 1001) < 0.01
    assert capture.get(cv2.CAP_PROP_FRAME_COUNT) == 30
    assert capture.get(cv2.CAP_PROP_FRAME_WIDTH) == 160
    assert capture.get(cv2.CAP_PROP_FRAME_HEIGHT) == 120
    ok, blurred = capture.read()
    assert ok
    capture.release()
    capture = cv2.VideoCapture(str(source))
    ok, original = capture.read()
    assert ok
    capture.release()
    difference = np.abs(blurred.astype(float) - original.astype(float)).mean()
    assert difference > 10 if faces else difference < 10
    # All frames and any audio must decode, with the expected final codecs.
    probe = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-i", str(result), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert "Video: h264" in probe.stderr
    assert ("Audio: aac" in probe.stderr) == audio
    print(f"PASS: {suffix}, audio={audio}, faces={faces}, 30 frames, fractional FPS, cleanup")


def main():
    with TemporaryDirectory(prefix="media-smoke-") as temp:
        for suffix in (".mp4", ".mov"):
            for audio in (False, True):
                run_case(Path(temp), suffix, audio)
        run_case(Path(temp), ".mp4", False, faces=False)


if __name__ == "__main__":
    main()
