import boto3

reko = boto3.client("rekognition")


def lambda_handler(event, context):
    response = reko.get_face_detection(JobId=event["job_id"], MaxResults=1)
    return {
        "statusCode": 200,
        "body": {
            "job_id": event["job_id"],
            "job_status": response["JobStatus"],
            "s3_object_bucket": event["s3_object_bucket"],
            "s3_object_key": event["s3_object_key"],
        },
    }
