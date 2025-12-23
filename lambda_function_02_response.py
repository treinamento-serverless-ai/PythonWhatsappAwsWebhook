import json
import os
import urllib3
import datetime
import boto3

# HTTP client
http = urllib3.PoolManager()

# Environment variables
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN', 'undefined_token')
ACCESS_TOKEN = os.environ.get('ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
API_VERSION = os.environ.get('VERSION', 'v22.0')

# Derived constants
WHATSAPP_API_URL = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
BUCKET_NAME = os.environ.get('BUCKET_NAME', 'your-bucket-name')  # Default bucket name

# --- Configuration and Clients ---
# AWS clients
s3_client = boto3.client('s3')

# --- Functions ---
def send_whatsapp_message(to_number, message_body):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {
            "body": message_body
        }
    }

    try:
        response = http.request("POST", WHATSAPP_API_URL, body=json.dumps(payload), headers=headers)
        print(f"Sent message to {to_number}. Response: {response.status}, {response.data.decode('utf-8')}")
    except Exception as e:
        print(f"Failed to send message: {str(e)}")


def lambda_handler(event, context):
    timestamp = datetime.datetime.utcnow().isoformat()
    body = event.get('body', '{}')
    request_body = event.get('body', '{}')

    # Store raw event and body separately for traceability
    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=f"event/{timestamp}.json",
            Body=json.dumps(event),
            ContentType='application/json'
        )

        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=f"body/{timestamp}.json",
            Body=request_body,
            ContentType='application/json'
        )
    except Exception as e:
        print(f"Failed to log request to S3: {type(e).__name__} - {str(e)}")


    http_method = event.get('httpMethod', '')
    
    if http_method == 'GET':
        params = event.get('queryStringParameters') or {}
        mode = params.get('hub.mode')
        token = params.get('hub.verify_token')
        challenge = params.get('hub.challenge')
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            return {
                'statusCode': 200,
                'body': challenge
            }
        else:
            return {
                'statusCode': 403,
                'body': json.dumps({'error': 'Invalid verification token'})
            }

    elif http_method == 'POST':
        try:
            parsed_body = json.loads(body)
            messages = parsed_body.get('entry', [])[0].get('changes', [])[0].get('value', {}).get('messages', [])

            if messages:
                message = messages[0]
                from_number = message.get('from')  # wa_id of the sender
                print(f"Incoming message from: {from_number}")
                send_whatsapp_message(from_number, "I received your message")

            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Message processed'})
            }

        except Exception as e:
            print(f"Error: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': str(e)})
            }

    else:
        return {
            'statusCode': 405,
            'body': json.dumps({'error': f'Method {http_method} not allowed'})
        }
