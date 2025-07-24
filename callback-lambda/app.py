import os
import json
import boto3
import requests

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('Submissions')

def handler(event, context):
    for record in event['Records']:
        result = json.loads(record['body'])
        submission_id = result['submissionId']
        status = result['status']
        
        table.update_item(
            Key={'submissionId': submission_id},
            UpdateExpression='SET #s = :s, #o = :o',
            ExpressionAttributeNames={'#s': 'status', '#o': 'output'},
            ExpressionAttributeValues={':s': status, ':o': result.get('output', '')}
        )
        
        if result.get('callbackUrl'):
            try:
                requests.post(result['callbackUrl'], json=result, timeout=5)
                print(f"Callback sent for {submission_id}")
            except requests.RequestException as e:
                print(f"Failed to send callback: {e}")
