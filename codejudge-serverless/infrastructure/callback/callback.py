import json
import os
import urllib.request

def handler(event, context):
    
    for record in event['Records']:
        payload = json.loads(record['body'])
        webhook_url = payload.get('callback_url')
        
        try:
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )

            with urllib.request.urlopen(req, timeout=5) as response:
                print(f"Webhook response status: {response.status}")
                
        except Exception as e:
            print(f"Failed to send webhook: {e}")
            raise e

    return {"statusCode": 200, "body": "Callback executed successfully"}