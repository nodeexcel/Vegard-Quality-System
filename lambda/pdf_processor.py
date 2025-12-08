"""
Lambda function to process PDF analysis jobs from SQS
"""
import json
import os
import boto3
import logging
from typing import Dict, Any

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='eu-north-1')

# Environment variables
S3_BUCKET = os.environ.get('S3_BUCKET_NAME', 'validert-tilstandsrapporter')
API_ENDPOINT = os.environ.get('API_ENDPOINT', 'https://www.validert.no/api')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process SQS messages containing PDF analysis jobs
    
    Expected message format:
    {
        "report_id": "uuid",
        "s3_key": "path/to/pdf",
        "user_email": "user@example.com"
    }
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    processed = 0
    failed = 0
    
    for record in event.get('Records', []):
        try:
            # Parse SQS message
            message_body = json.loads(record['body'])
            report_id = message_body['report_id']
            s3_key = message_body['s3_key']
            user_email = message_body.get('user_email', 'unknown')
            
            logger.info(f"Processing report {report_id} for user {user_email}")
            
            # Download PDF from S3
            pdf_response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            pdf_content = pdf_response['Body'].read()
            
            logger.info(f"Downloaded PDF: {len(pdf_content)} bytes")
            
            # TODO: Process the PDF
            # 1. Extract text from PDF
            # 2. Analyze with Bedrock Claude
            # 3. Store results in database via API callback
            
            # For now, just log success
            logger.info(f"✅ Successfully processed report {report_id}")
            processed += 1
            
        except Exception as e:
            logger.error(f"❌ Failed to process record: {str(e)}")
            failed += 1
            # Don't raise - let other messages in batch process
            continue
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed': processed,
            'failed': failed,
            'total': len(event.get('Records', []))
        })
    }

