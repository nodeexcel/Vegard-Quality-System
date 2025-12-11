"""
Lambda function to process PDF analysis jobs from SQS
"""
import json
import os
import boto3
import logging
import io
import requests
from typing import Dict, Any

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='eu-north-1')

# Environment variables
S3_BUCKET = os.environ.get('S3_BUCKET_NAME', 'validert-tilstandsrapporter')
API_ENDPOINT = os.environ.get('API_ENDPOINT', 'https://www.verifisert.no/api')


def extract_text_from_pdf(pdf_content: bytes) -> str:
    """
    Extract text from PDF using PyPDF2
    Note: PyPDF2 needs to be included in Lambda layer
    """
    try:
        import PyPDF2
        pdf_file = io.BytesIO(pdf_content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        
        logger.info(f"Extracted {len(text)} characters from PDF")
        return text
    except Exception as e:
        logger.error(f"PDF extraction error: {str(e)}")
        raise


def analyze_with_bedrock(text: str) -> Dict:
    """
    Analyze report text using Bedrock Claude
    """
    try:
        # Build prompt
        system_prompt = """Du er en ekspert på norske tilstandsrapporter for bygninger. 
Analyser rapporten og gi en kvalitetsvurdering basert på NS 3600:2018 og relevante forskrifter.

Returner ONLY valid JSON i dette formatet:
{
  "overall_score": 75.0,
  "quality_score": 80.0,
  "completeness_score": 70.0,
  "compliance_score": 75.0,
  "components": [
    {
      "component_type": "Tak",
      "name": "Taktekkjing",
      "condition": "god",
      "description": "...",
      "score": 85.0
    }
  ],
  "findings": [
    {
      "finding_type": "mangler",
      "severity": "medium",
      "title": "...",
      "description": "...",
      "suggestion": "...",
      "standard_reference": "NS 3600:2018"
    }
  ],
  "summary": "...",
  "recommendations": ["...", "..."]
}"""
        
        user_message = f"Analyser følgende tilstandsrapport:\n\n{text[:30000]}"
        
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "temperature": 0.3,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": user_message
                }
            ]
        })
        
        logger.info("Invoking Bedrock Claude...")
        response = bedrock_runtime.invoke_model(
            modelId='eu.anthropic.claude-sonnet-4-20250514-v1:0',
            body=body,
            contentType='application/json',
            accept='application/json'
        )
        
        response_body = json.loads(response['body'].read())
        content = response_body.get('content', [])
        
        if content and len(content) > 0:
            response_text = content[0].get('text', '')
            
            # Extract JSON
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            
            if json_start != -1 and json_end > json_start:
                json_text = response_text[json_start:json_end]
                analysis_data = json.loads(json_text)
                logger.info("Successfully analyzed report with Bedrock")
                return analysis_data
            else:
                raise ValueError("Could not find JSON in response")
        else:
            raise ValueError("No content in Bedrock response")
            
    except Exception as e:
        logger.error(f"Bedrock analysis error: {str(e)}")
        raise


def update_report_via_api(report_id: int, analysis_data: Dict) -> bool:
    """
    Update report in database via API callback
    """
    try:
        url = f"{API_ENDPOINT}/v1/reports/{report_id}/update-analysis"
        
        payload = {
            "overall_score": analysis_data.get("overall_score", 0.0),
            "quality_score": analysis_data.get("quality_score", 0.0),
            "completeness_score": analysis_data.get("completeness_score", 0.0),
            "compliance_score": analysis_data.get("compliance_score", 0.0),
            "components": analysis_data.get("components", []),
            "findings": analysis_data.get("findings", []),
            "ai_analysis": analysis_data
        }
        
        response = requests.post(url, json=payload, timeout=30)
        
        if response.status_code == 200:
            logger.info(f"Successfully updated report {report_id}")
            return True
        else:
            logger.error(f"API update failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"API callback error: {str(e)}")
        return False


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
    logger.info(f"Received event with {len(event.get('Records', []))} messages")
    
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
            
            # Step 1: Download PDF from S3
            logger.info(f"Downloading PDF from S3: {s3_key}")
            pdf_response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            pdf_content = pdf_response['Body'].read()
            logger.info(f"Downloaded PDF: {len(pdf_content)} bytes")
            
            # Step 2: Extract text from PDF
            logger.info("Extracting text from PDF...")
            text = extract_text_from_pdf(pdf_content)
            
            if len(text.strip()) < 100:
                raise ValueError("Insufficient text extracted from PDF")
            
            # Step 3: Analyze with Bedrock
            logger.info("Analyzing with Bedrock Claude...")
            analysis_data = analyze_with_bedrock(text)
            
            # Step 4: Update database via API
            logger.info("Updating report in database...")
            success = update_report_via_api(report_id, analysis_data)
            
            if success:
                logger.info(f"✅ Successfully processed report {report_id}")
                processed += 1
            else:
                raise Exception("Failed to update database")
            
        except Exception as e:
            logger.error(f"❌ Failed to process record: {str(e)}")
            failed += 1
            # Continue processing other messages
            continue
    
    return {
        'statusCode': 200 if failed == 0 else 207,
        'body': json.dumps({
            'processed': processed,
            'failed': failed,
            'total': len(event.get('Records', []))
        })
    }
