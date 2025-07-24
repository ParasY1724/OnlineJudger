import os
import json
import uuid
import datetime
import boto3

dynamodb = boto3.resource('dynamodb')
sqs = boto3.client('sqs')
table = dynamodb.Table('Submissions')
submission_queue_url = os.environ['SUBMISSION_QUEUE_URL']

def handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        submission_id = str(uuid.uuid4())
        
        table.put_item(
            Item={
                'submissionId': submission_id,
                'status': 'PENDING',
                'createdAt': datetime.datetime.utcnow().isoformat(),
                'language': body['language'],
                'callbackUrl': body['callbackUrl']
            }
        )
        
        body['submissionId'] = submission_id
        
        sqs.send_message(
            QueueUrl=submission_queue_url,
            MessageBody=json.dumps(body)
        )
        
        return {'statusCode': 202, 'body': json.dumps({'submissionId': submission_id})}
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
