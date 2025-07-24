import os
import json
import boto3

ecs = boto3.client('ecs')

def handler(event, context):
    cluster = os.environ['ECS_CLUSTER_NAME']
    task_definition = os.environ['ECS_TASK_DEFINITION']
    result_queue_url = os.environ['RESULT_QUEUE_URL']
    subnet_id = os.environ['SUBNET_ID'] # A default subnet from your VPC

    for record in event['Records']:
        submission = json.loads(record['body'])
        
        ecs.run_task(
            cluster=cluster,
            taskDefinition=task_definition,
            launchType="FARGATE",
            networkConfiguration={
                'awsvpcConfiguration': {
                    'subnets': [subnet_id],
                    'assignPublicIp': 'ENABLED'
                }
            },
            overrides={
                'containerOverrides': [{
                    'name': 'judge-container',
                    'environment': [
                        {'name': 'SUBMISSION_ID', 'value': submission['submissionId']},
                        {'name': 'SOURCE_CODE', 'value': submission['sourceCode']},
                        {'name': 'LANGUAGE', 'value': submission['language']},
                        {'name': 'INPUT', 'value': submission['input']},
                        {'name': 'EXPECTED_OUTPUT', 'value': submission['expectedOutput']},
                        {'name': 'CALLBACK_URL', 'value': submission['callbackUrl']},
                        {'name': 'RESULT_QUEUE_URL', 'value': result_queue_url}
                    ]
                }]
            }
        )
        print(f"Dispatched task for submission: {submission['submissionId']}")
