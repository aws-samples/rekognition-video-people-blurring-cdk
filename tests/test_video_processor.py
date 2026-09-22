from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest


@pytest.mark.parametrize("left,top", [(-0.1, 0.2), (0.2, -0.1), (0.9, 0.2), (0.2, 0.9)])
def test_faces_crossing_each_border_are_blurred(video_processor, left, top):
    frame = np.random.default_rng(42).integers(0, 256, (100, 100, 3), dtype=np.uint8)
    before = frame.copy()
    video_processor.blur_faces_in_frame(
        frame, [{"Left": left, "Top": top, "Width": 0.3, "Height": 0.3}]
    )
    assert not np.array_equal(frame, before)
    assert np.array_equal(frame[50:70, 50:70], before[50:70, 50:70])
    assert frame.var() < before.var()


@pytest.mark.parametrize(
    "box",
    [
        {"Left": -2, "Top": 0, "Width": 0.1, "Height": 0.1},
        {"Left": 2, "Top": 0, "Width": 0.1, "Height": 0.1},
        {"Left": 0, "Top": 0, "Width": 0, "Height": 0},
    ],
)
def test_outside_or_empty_boxes_do_not_change_frame(video_processor, box):
    frame = np.ones((20, 20, 3), dtype=np.uint8)
    assert np.array_equal(video_processor.blur_faces_in_frame(frame.copy(), [box]), frame)


def test_tiny_faces_and_empty_regions(video_processor):
    image = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)
    blurred = video_processor.anonymize_face_pixelate(image)
    assert np.array_equal(blurred[0, 0], blurred[1, 1])
    assert video_processor.anonymize_face_pixelate(image[:0]).size == 0


def test_invalid_box_fails(video_processor):
    with pytest.raises(ValueError):
        video_processor.blur_faces_in_frame(
            np.zeros((10, 10, 3), dtype=np.uint8),
            [{"Left": float("nan"), "Top": 0, "Width": 1, "Height": 1}],
        )


def test_frame_rate_and_detection_windows(video_processor, monkeypatch, tmp_path):
    capture = Mock()
    capture.isOpened.return_value = True
    capture.get.return_value = 32
    capture.read.side_effect = [
        (True, np.zeros((20, 20, 3), dtype=np.uint8)) for _ in range(32)
    ] + [(False, None)]
    output = tmp_path / "out.avi"
    output.write_bytes(b"video")
    writer = Mock()
    monkeypatch.setattr(video_processor.cv2, "VideoCapture", Mock(return_value=capture))
    factory = Mock(return_value=writer)
    monkeypatch.setattr(video_processor.cv2, "VideoWriter", factory)
    blur = Mock()
    monkeypatch.setattr(video_processor, "blur_faces_in_frame", blur)
    video_processor.apply_faces_to_video(
        {"500": [{"face": True}]},
        "input.mp4",
        output,
        {"FrameRate": 29.97, "FrameWidth": 20, "FrameHeight": 20},
    )
    assert factory.call_args.args[2] == 29.97
    assert writer.write.call_count == 32
    assert blur.call_count == 17  # original inclusive frames 14 through 30
    capture.release.assert_called_once()
    writer.release.assert_called_once()


def test_unreadable_input_fails(video_processor, tmp_path):
    with pytest.raises(RuntimeError, match="Unable to open"):
        video_processor.apply_faces_to_video(
            {}, "missing.mp4", tmp_path / "out.avi", {"FrameRate": 30}
        )


def test_decode_failure_releases_resources(video_processor, monkeypatch, tmp_path):
    capture = Mock()
    capture.isOpened.return_value = True
    capture.get.return_value = 10
    capture.read.side_effect = [(True, np.zeros((20, 20, 3), dtype=np.uint8)), (False, None)]
    writer = Mock()
    monkeypatch.setattr(video_processor.cv2, "VideoCapture", Mock(return_value=capture))
    monkeypatch.setattr(video_processor.cv2, "VideoWriter", Mock(return_value=writer))
    with pytest.raises(RuntimeError, match="decoded completely"):
        video_processor.apply_faces_to_video(
            {},
            "in.mp4",
            tmp_path / "out.avi",
            {"FrameRate": 30, "FrameWidth": 20, "FrameHeight": 20},
        )
    capture.release.assert_called_once()
    writer.release.assert_called_once()


def test_ffmpeg_failure_does_not_replace_output(video_processor, tmp_path):
    import subprocess

    output = tmp_path / "out.mp4"
    output.write_bytes(b"original")
    with pytest.raises(subprocess.CalledProcessError):
        video_processor.integrate_audio(tmp_path / "missing.mp4", output)
    assert output.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [Path(output)]


def test_odd_dimensions_preserve_edge_pixels_and_overlapping_windows(
    video_processor, monkeypatch, tmp_path
):
    # Multiple timestamps can map to the same frame; their face dictionaries
    # must not be used as sort keys.
    frame = np.full((21, 23, 3), 77, dtype=np.uint8)
    capture = Mock()
    capture.isOpened.return_value = True
    capture.get.return_value = 1
    capture.read.side_effect = [(True, frame), (False, None)]
    writer = Mock()
    factory = Mock(return_value=writer)
    monkeypatch.setattr(video_processor.cv2, "VideoCapture", Mock(return_value=capture))
    monkeypatch.setattr(video_processor.cv2, "VideoWriter", factory)
    output = tmp_path / "out.avi"
    output.write_bytes(b"video")
    boxes = [{"Left": 0, "Top": 0, "Width": 0.5, "Height": 0.5}]
    count = video_processor.apply_faces_to_video(
        {"0": boxes, "1": boxes},
        "in.mp4",
        output,
        {"FrameRate": 30, "FrameWidth": 23, "FrameHeight": 21},
    )
    assert count == 1
    assert factory.call_args.args[3] == (24, 22)
    written = writer.write.call_args.args[0]
    assert written.shape == (22, 24, 3)
    assert np.all(written == 77)


def test_truncated_encoding_is_rejected(video_processor, monkeypatch, tmp_path):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"original")
    output = tmp_path / "output.mp4"

    def encode(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial video")

    monkeypatch.setattr(video_processor.subprocess, "run", encode)
    capture = Mock()
    capture.isOpened.return_value = True
    capture.get.return_value = 2
    monkeypatch.setattr(video_processor.cv2, "VideoCapture", Mock(return_value=capture))
    with pytest.raises(RuntimeError, match="all input frames"):
        video_processor.integrate_audio(original, original, output, expected_frames=30)
    assert not output.exists()
    assert original.read_bytes() == b"original"
    capture.release.assert_called_once()
