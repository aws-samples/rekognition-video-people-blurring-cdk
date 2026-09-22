import logging
import math
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import imageio_ffmpeg

logger = logging.getLogger(__name__)


def anonymize_face_pixelate(image, blocks=10):
    """Pixelate a face, including regions smaller than the requested grid."""
    if image.size == 0:
        return image
    if blocks < 1:
        raise ValueError("blocks must be positive")
    height, width = image.shape[:2]
    small = cv2.resize(
        image,
        (min(blocks, max(1, width // 2)), min(blocks, max(1, height // 2))),
        interpolation=cv2.INTER_AREA,
    )
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)


def blur_faces_in_frame(frame, faces):
    """Clip both corners of each padded bounding box to the actual frame."""
    height, width = frame.shape[:2]
    width_delta, height_delta = int(width / 250), int(height / 100)
    for face in faces:
        left, top = float(face["Left"]), float(face["Top"])
        box_width, box_height = float(face["Width"]), float(face["Height"])
        if not all(math.isfinite(v) for v in (left, top, box_width, box_height)):
            raise ValueError("Non-finite face bounding box")
        if box_width <= 0 or box_height <= 0:
            continue
        x1 = max(0, math.floor(left * width) - width_delta)
        y1 = max(0, math.floor(top * height) - height_delta)
        x2 = min(width, math.ceil((left + box_width) * width) + width_delta)
        y2 = min(height, math.ceil((top + box_height) * height) + height_delta)
        if x2 > x1 and y2 > y1:
            frame[y1:y2, x1:x2] = anonymize_face_pixelate(frame[y1:y2, x1:x2])
    return frame


def apply_faces_to_video(final_timestamps, local_path_to_video, local_output, video_metadata):
    frame_rate = float(video_metadata["FrameRate"])
    if not math.isfinite(frame_rate) or frame_rate <= 0:
        raise ValueError("Video frame rate must be positive and finite")
    # Preserve the original half-second forward detection window. Sort once,
    # then visit only detections active in the current frame (not the full video).
    windows = sorted(
        (
            (
                int(int(timestamp) / 1000 * frame_rate),
                int(int(timestamp) / 1000 * frame_rate + frame_rate / 2) + 1,
                faces,
            )
            for timestamp, faces in final_timestamps.items()
        ),
        key=lambda window: window[0],
    )
    capture = cv2.VideoCapture(str(local_path_to_video))
    writer = None
    frame_counter = 0
    next_window = 0
    active = []
    try:
        if not capture.isOpened():
            raise RuntimeError("Unable to open input video")
        expected_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        while True:
            has_frame, frame = capture.read()
            if not has_frame:
                break
            height, width = frame.shape[:2]
            if writer is None:
                if (width, height) != (video_metadata["FrameWidth"], video_metadata["FrameHeight"]):
                    raise ValueError("Decoded dimensions do not match Rekognition metadata")
                # Use an AVI intermediate; the final output is encoded as H.264.
                writer = cv2.VideoWriter(
                    str(local_output),
                    cv2.VideoWriter_fourcc(*"MJPG"),
                    frame_rate,
                    (width + width % 2, height + height % 2),
                )
                if not writer.isOpened():
                    raise RuntimeError("Unable to open output video writer")
            while next_window < len(windows) and windows[next_window][0] <= frame_counter:
                active.append(windows[next_window])
                next_window += 1
            active = [window for window in active if window[1] >= frame_counter]
            for _, _, faces in active:
                blur_faces_in_frame(frame, faces)
            # OpenCV's FFmpeg backend otherwise truncates the last odd row/column.
            if width % 2 or height % 2:
                frame = cv2.copyMakeBorder(frame, 0, height % 2, 0, width % 2, cv2.BORDER_REPLICATE)
            writer.write(frame)
            frame_counter += 1
        if frame_counter == 0:
            raise RuntimeError("Input video contains no decodable frames")
        if expected_frames > 0 and frame_counter < expected_frames:
            raise RuntimeError("Input video could not be decoded completely")
    finally:
        capture.release()
        if writer is not None:
            writer.release()
    if not Path(local_output).is_file() or Path(local_output).stat().st_size == 0:
        raise RuntimeError("Video writer produced no output")
    logger.info("Blurred %d frames", frame_counter)
    return frame_counter


def integrate_audio(original_video, output_video, final_output=None, expected_frames=None):
    """Encode H.264 and copy optional original audio as AAC; fail on any error.

    Two-argument calls retain the original replace-in-place interface. The
    Lambda supplies a separate final output so all intermediates can be cleaned.
    """
    output_video = Path(output_video)
    destination = Path(final_output) if final_output else output_video
    with TemporaryDirectory(prefix="encode-", dir=output_video.parent) as directory:
        encoded = Path(directory) / (
            "encoded.mov" if destination.suffix.lower() == ".mov" else "encoded.mp4"
        )
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-xerror",
                "-i",
                str(output_video),
                "-i",
                str(original_video),
                "-map",
                "0:v:0",
                "-map",
                "1:a?",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-pix_fmt",
                "yuv420p",
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(encoded),
            ],
            check=True,
            timeout=840,
        )
        if not encoded.is_file() or encoded.stat().st_size == 0:
            raise RuntimeError("FFmpeg produced no output")
        if expected_frames is not None:
            capture = cv2.VideoCapture(str(encoded))
            try:
                if (
                    not capture.isOpened()
                    or int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) != expected_frames
                ):
                    raise RuntimeError("Encoded video does not contain all input frames")
            finally:
                capture.release()
        os.replace(encoded, destination)
