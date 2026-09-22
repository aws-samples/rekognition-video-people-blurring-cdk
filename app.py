#!/usr/bin/env python3
import aws_cdk as cdk

from stack.rekognition_video_face_blurring_cdk_stack import RekognitionVideoFaceBlurringCdkStack

app = cdk.App()
RekognitionVideoFaceBlurringCdkStack(app, "RekognitionVideoFaceBlurringCdkStack")
app.synth()
