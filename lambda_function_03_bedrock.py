import json
import os
import datetime
import logging
from typing import Optional, Dict, Any

import urllib3
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# HTTP client
http = urllib3.PoolManager()

# Logging setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN', 'undefined_token')
ACCESS_TOKEN = os.environ.get('ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
API_VERSION = os.environ.get('API_VERSION', 'v22.0')
BEDROCK_AGENT_ID = os.environ.get("BEDROCK_AGENT_ID")
BEDROCK_ALIAS_ID = os.environ.get("BEDROCK_ALIAS_ID", "TSTALIASID")

# Derived constants
WHATSAPP_API_URL = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
BUCKET_NAME = os.environ.get('BUCKET_NAME', 'your-bucket-name')  # Default bucket name

# --- Configuration and Clients ---
# AWS clients
s3_client = boto3.client('s3')
bedrock_agent_runtime_client = boto3.client(
    'bedrock-agent-runtime',
    config=Config(read_timeout=600, connect_timeout=10)
)

# --- Functions ---
def send_whatsapp_message(to_number: str, message_body: str) -> None:
    """
    Sends a text message to a given WhatsApp number using the Meta Graph API.

    Args:
        to_number (str): WhatsApp number in international format.
        message_body (str): Text message to send.
    """
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
        logger.info(f"Sent message to {to_number}. Status: {response.status}, Response: {response.data.decode('utf-8')}")
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message: {str(e)}")


def invoke_agent(query: str, current_conversation_id: str, session_state: Optional[Dict[str, Any]] = None) -> str:
    """
    Calls the Bedrock agent with the given query and session context.

    Args:
        query (str): User input to the agent.
        current_conversation_id (str): Unique identifier for the conversation session.
        session_state (dict, optional): Optional session state to maintain context.

    Returns:
        str: The agent's full response or error message.
    """
    invoke_args = {
        "agentId": BEDROCK_AGENT_ID,
        "agentAliasId": BEDROCK_ALIAS_ID,
        "sessionId": current_conversation_id,
        "inputText": query
    }

    if session_state:
        invoke_args["sessionState"] = session_state

    try:
        response = bedrock_agent_runtime_client.invoke_agent(**invoke_args)
        completion = response.get('completion')

        if not completion:
            return "No response from the agent."

        full_response = ""
        for event in completion:
            if 'chunk' in event and 'bytes' in event['chunk']:
                full_response += event['chunk']['bytes'].decode('utf-8')
            elif 'trace' in event:
                logger.debug(f"Agent trace: {event['trace']}")
            else:
                logger.warning(f"Unexpected event format: {event}")

        return full_response

    except ClientError as error:
        logger.error(f"Error invoking agent: {error}")
        return f"Error invoking agent: {error}"


def store_event_to_s3(prefix: str, data: Any) -> None:
    """
    Persists JSON-serializable data to S3 for logging and traceability.

    Args:
        prefix (str): S3 object prefix (e.g., 'event', 'body').
        data (Any): The data to serialize and store.
    """
    timestamp = datetime.datetime.utcnow().isoformat()
    key = f"{prefix}/{timestamp}.json"

    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=json.dumps(data),
            ContentType='application/json'
        )
        logger.info(f"Stored {prefix} data in S3 at {key}")
    except Exception as e:
        logger.error(f"Failed to store {prefix} data to S3: {type(e).__name__} - {str(e)}")


# --- Lambda Handler ---

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda entry point for handling WhatsApp webhook requests.

    Args:
        event (dict): API Gateway Lambda Proxy Input Format.
        context: Lambda Context runtime methods and attributes.

    Returns:
        dict: API Gateway-compatible HTTP response.
    """
    http_method = event.get('httpMethod', '')
    raw_body = event.get('body', '{}')

    # Persist incoming request to S3
    store_event_to_s3('event', event)
    store_event_to_s3('body', raw_body)

    if http_method == 'GET':
        # Handle webhook verification challenge
        params = event.get('queryStringParameters') or {}
        mode = params.get('hub.mode')
        token = params.get('hub.verify_token')
        challenge = params.get('hub.challenge')

        if mode == 'subscribe' and token == VERIFY_TOKEN:
            return {'statusCode': 200, 'body': challenge}
        else:
            return {'statusCode': 403, 'body': json.dumps({'error': 'Invalid verification token'})}

    elif http_method == 'POST':
        try:
            parsed_body = json.loads(raw_body)
            messages = (
                parsed_body.get('entry', [])[0]
                .get('changes', [])[0]
                .get('value', {})
                .get('messages', [])
            )

            if not messages:
                return {'statusCode': 200, 'body': json.dumps({'message': 'No message object found in webhook'})}

            message = messages[0]
            from_number = message.get('from')
            text_message = message.get('text', {}).get('body', '')

            logger.info(f"Received message from: {from_number}")

            assistant_response = invoke_agent(
                query=text_message,
                current_conversation_id=from_number,
                session_state=None
            )

            send_whatsapp_message(from_number, assistant_response)

            return {'statusCode': 200, 'body': json.dumps({'message': 'Message processed'})}

        except Exception as e:
            logger.error(f"Error processing POST request: {str(e)}")
            return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}

    else:
        return {'statusCode': 405, 'body': json.dumps({'error': f'Method {http_method} not allowed'})}
