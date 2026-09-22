import json
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stack.rekognition_video_face_blurring_cdk_stack import RekognitionVideoFaceBlurringCdkStack


@pytest.fixture(scope="module")
def template():
    config = json.loads((Path(__file__).resolve().parents[1] / "cdk.json").read_text())
    app = cdk.App(context=config.get("context", {}))
    stack = RekognitionVideoFaceBlurringCdkStack(app, "RekognitionVideoFaceBlurringCdkStack")
    return Template.from_stack(stack)


def test_all_application_lambdas_use_supported_runtime(template):
    functions = template.find_resources("AWS::Lambda::Function")
    managed = [
        resource["Properties"]
        for key, resource in functions.items()
        if key.startswith(
            ("StartFaceDetectFunction", "CheckStatusFunction", "GetTimestampsFunction")
        )
    ]
    assert len(managed) == 3
    assert all(function["Runtime"] == "python3.14" for function in managed)
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "PackageType": "Image",
            "Architectures": ["x86_64"],
            "EphemeralStorage": {"Size": 10240},
            "Timeout": 900,
        },
    )


def test_buckets_are_private_encrypted_and_retained(template):
    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 3
    for bucket in buckets.values():
        assert bucket["DeletionPolicy"] == "Retain"
        assert bucket["Properties"]["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
        assert (
            bucket["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
                "ServerSideEncryptionByDefault"
            ]["SSEAlgorithm"]
            == "AES256"
        )
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "LifecycleConfiguration": {"Rules": [Match.object_like({"ExpirationInDays": 7})]},
        },
    )
    assert len(template.find_resources("AWS::S3::BucketPolicy")) == 3


def test_notifications_outputs_and_workflow_are_preserved(template):
    notifications = next(iter(template.find_resources("Custom::S3BucketNotifications").values()))
    configurations = notifications["Properties"]["NotificationConfiguration"][
        "LambdaFunctionConfigurations"
    ]
    assert {item["Filter"]["Key"]["FilterRules"][0]["Value"] for item in configurations} == {
        ".mp4",
        ".mov",
    }
    assert all(item["Events"] == ["s3:ObjectCreated:*"] for item in configurations)
    outputs = template.to_json()["Outputs"]
    assert {"InputBucketName", "OutputBucketName", "StateMachineArn"} <= outputs.keys()
    template.has_resource_properties(
        "AWS::StepFunctions::StateMachine", {"DefinitionString": Match.any_value()}
    )
    machine = next(iter(template.find_resources("AWS::StepFunctions::StateMachine").values()))
    # Resolve token fragments only enough to inspect the service-independent ASL.
    fragments = machine["Properties"]["DefinitionString"]["Fn::Join"][1]
    definition = json.loads(
        "".join(part if isinstance(part, str) else "TOKEN" for part in fragments)
    )
    assert definition["TimeoutSeconds"] == 3600
    states = definition["States"]
    assert states["Wait 1 Second"]["Seconds"] == 5
    assert states["Wait 1 Second"]["Next"] == "Check Job Status"
    assert states["Job finished?"]["Default"] == "Execution Failed"
    assert states["Get Timestamps and Faces"]["Next"] == "Blur Faces on Video"
    assert states["Blur Faces on Video"]["Next"] == "Execution Succeeded"


def test_start_role_cannot_write_input_objects(template):
    policies = template.find_resources("AWS::IAM::Policy")
    start = next(
        resource
        for name, resource in policies.items()
        if name.startswith("StartFaceDetectFunction")
    )
    statements = start["Properties"]["PolicyDocument"]["Statement"]
    for statement in statements:
        actions = statement["Action"]
        if isinstance(actions, str):
            actions = [actions]
        assert "s3:PutObject" not in actions
        if "states:StartExecution" in actions:
            assert statement["Resource"] != "*"


def test_existing_resource_ids_are_preserved(template):
    # Verified against a synthesis of main at bf7c162 using CDK 2.103.1.
    resources = template.to_json()["Resources"]
    expected = {
        "InputImageBucket20B2BA6B": "AWS::S3::Bucket",
        "OutputImageBucket13118363": "AWS::S3::Bucket",
        "StartFaceDetectFunction04E44367": "AWS::Lambda::Function",
        "CheckStatusFunction07592B85": "AWS::Lambda::Function",
        "GetTimestampsFunctionDFA6255F": "AWS::Lambda::Function",
        "BlurFacesFunctionB4E0F809": "AWS::Lambda::Function",
        "StateMachine2E01A3A5": "AWS::StepFunctions::StateMachine",
    }
    for logical_id, resource_type in expected.items():
        assert resources[logical_id]["Type"] == resource_type
