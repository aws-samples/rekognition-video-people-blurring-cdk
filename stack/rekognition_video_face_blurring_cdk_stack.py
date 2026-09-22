from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Size,
    Stack,
)
from aws_cdk import (
    aws_ecr_assets as ecr_assets,
)
from aws_cdk import (
    aws_iam as _iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from aws_cdk import (
    aws_stepfunctions_tasks as tasks,
)
from aws_cdk.aws_lambda_event_sources import S3EventSource
from constructs import Construct


class RekognitionVideoFaceBlurringCdkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        lambda_dir = Path(__file__).parent / "lambdas"

        ###############################################################################################
        # S3#
        ###############################################################################################

        ## S3 buckets for input and output locations
        bucket_options = {
            "block_public_access": s3.BlockPublicAccess.BLOCK_ALL,
            "encryption": s3.BucketEncryption.S3_MANAGED,
            "enforce_ssl": True,
        }
        inputImageBucket = s3.Bucket(self, "InputImageBucket", **bucket_options)
        outputImageBucket = s3.Bucket(self, "OutputImageBucket", **bucket_options)
        detectionBucket = s3.Bucket(
            self,
            "DetectionResultsBucket",
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(7))],
            **bucket_options,
        )

        ###############################################################################################
        # Lambda#
        ###############################################################################################

        ## Lambda triggering the Rekognition job and the StepFunctions workflow
        startFaceDetectFunction = lambda_.Function(
            self,
            "StartFaceDetectFunction",
            timeout=Duration.seconds(600),
            memory_size=512,
            code=lambda_.Code.from_asset(
                str(lambda_dir / "rekopoc-start-face-detect"), exclude=["__pycache__", "*.pyc"]
            ),
            handler="lambda_function.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_14,
        )

        # Adding S3 event sources triggers for the startFaceDetectFunction, allowing .mov and .mp4 files only
        startFaceDetectFunction.add_event_source(
            S3EventSource(
                inputImageBucket,
                events=[s3.EventType.OBJECT_CREATED],
                filters=[s3.NotificationKeyFilter(suffix=".mov")],
            )
        )
        startFaceDetectFunction.add_event_source(
            S3EventSource(
                inputImageBucket,
                events=[s3.EventType.OBJECT_CREATED],
                filters=[s3.NotificationKeyFilter(suffix=".mp4")],
            )
        )

        # Allowing startFaceDetectFunction to access the S3 input bucket
        inputImageBucket.grant_read(startFaceDetectFunction)

        # Allowing startFaceDetectFunction to call Rekognition
        startFaceDetectFunction.add_to_role_policy(
            _iam.PolicyStatement(
                effect=_iam.Effect.ALLOW,
                actions=["rekognition:StartFaceDetection"],
                resources=["*"],
            )
        )

        ## Lambda checking Rekognition job status
        checkStatusFunction = lambda_.Function(
            self,
            "CheckStatusFunction",
            timeout=Duration.seconds(600),
            memory_size=512,
            code=lambda_.Code.from_asset(
                str(lambda_dir / "rekopoc-check-status"), exclude=["__pycache__", "*.pyc"]
            ),
            handler="lambda_function.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_14,
        )

        # Allowing checkStatusFunction to call Rekognition
        checkStatusFunction.add_to_role_policy(
            _iam.PolicyStatement(
                effect=_iam.Effect.ALLOW, actions=["rekognition:GetFaceDetection"], resources=["*"]
            )
        )

        ## Lambda getting data from Rekognition
        getTimestampsFunction = lambda_.Function(
            self,
            "GetTimestampsFunction",
            timeout=Duration.seconds(600),
            memory_size=512,
            code=lambda_.Code.from_asset(
                str(lambda_dir / "rekopoc-get-timestamps-faces"), exclude=["__pycache__", "*.pyc"]
            ),
            handler="lambda_function.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_14,
            environment={"DETECTION_BUCKET": detectionBucket.bucket_name},
        )
        detectionBucket.grant_put(getTimestampsFunction)

        # Allowing getTimestampsFunction to call Rekognition
        getTimestampsFunction.add_to_role_policy(
            _iam.PolicyStatement(
                effect=_iam.Effect.ALLOW, actions=["rekognition:GetFaceDetection"], resources=["*"]
            )
        )

        ## Lambda blurring the faces on the video based on Rekognition data
        blurFacesFunction = lambda_.DockerImageFunction(
            self,
            "BlurFacesFunction",
            timeout=Duration.minutes(15),
            memory_size=2048,
            ephemeral_storage_size=Size.gibibytes(10),
            architecture=lambda_.Architecture.X86_64,
            code=lambda_.DockerImageCode.from_image_asset(
                str(lambda_dir / "rekopoc-apply-faces-to-video-docker"),
                platform=ecr_assets.Platform.LINUX_AMD64,
            ),
        )

        # Adding the S3 output bucket name as an ENV variable to the blurFacesFunction
        blurFacesFunction.add_environment(key="OUTPUT_BUCKET", value=outputImageBucket.bucket_name)

        # Allowing blurFacesFunction to access the S3 input and output buckets
        inputImageBucket.grant_read(blurFacesFunction)
        outputImageBucket.grant_put(blurFacesFunction)
        detectionBucket.grant_read(blurFacesFunction)

        ###############################################################################################
        # StepFunctions#
        ###############################################################################################

        ## Retain the state ID for existing deployments; poll every five seconds.
        wait_1 = sfn.Wait(self, "Wait 1 Second", time=sfn.WaitTime.duration(Duration.seconds(5)))

        ## State in case of execution failure
        job_failed = sfn.Fail(
            self,
            "Execution Failed",
            cause="Face Detection Failed",
            error="Could not get job_status = 'SUCCEEDED'",
        )

        ## State in case of execution success
        job_succeeded = sfn.Succeed(self, "Execution Succeeded")

        ## Task checking the Rekognition job status
        update_job_status = tasks.LambdaInvoke(
            self,
            "Check Job Status",
            lambda_function=checkStatusFunction,
            # Lambda's result is in the attribute `Payload`
            input_path="$.body",
            output_path="$.Payload",
        )

        ## Task getting the data from Rekognition once the update_job_status task is a success
        get_timestamps_and_faces = tasks.LambdaInvoke(
            self,
            "Get Timestamps and Faces",
            lambda_function=getTimestampsFunction,
            input_path="$.body",
            output_path="$.Payload",
        )

        ## Task blurring the faces appearing on the video based on the get_timestamps_and_faces data
        blur_faces = tasks.LambdaInvoke(
            self,
            "Blur Faces on Video",
            lambda_function=blurFacesFunction,
            input_path="$.body",
            output_path="$.Payload",
        )

        ## Defining a choice
        choice = sfn.Choice(self, "Job finished?")

        # Adding conditions with .when()
        choice.when(
            sfn.Condition.string_equals("$.body.job_status", "IN_PROGRESS"),
            wait_1.next(update_job_status),
        )
        choice.when(
            sfn.Condition.string_equals("$.body.job_status", "SUCCEEDED"),
            get_timestamps_and_faces.next(blur_faces).next(job_succeeded),
        )
        # Adding a default choice with .otherwise() if none of the above choices are matched
        choice.otherwise(job_failed)

        ## Definition of the State Machine
        definition_body = sfn.DefinitionBody.from_chainable(update_job_status.next(choice))

        ## Actuel State Machine built with the above definition
        stateMachine = sfn.StateMachine(
            self, "StateMachine", definition_body=definition_body, timeout=Duration.hours(1)
        )

        ## Adding the State Machine ARN to the ENV variables of the Lambda startFaceDetectFunction
        startFaceDetectFunction.add_environment(
            key="STATE_MACHINE_ARN", value=stateMachine.state_machine_arn
        )

        # Allowing startFaceDetectFunction to start the StepFunctions workflow
        stateMachine.grant_start_execution(startFaceDetectFunction)
        CfnOutput(self, "InputBucketName", value=inputImageBucket.bucket_name)
        CfnOutput(self, "OutputBucketName", value=outputImageBucket.bucket_name)
        CfnOutput(self, "StateMachineArn", value=stateMachine.state_machine_arn)
