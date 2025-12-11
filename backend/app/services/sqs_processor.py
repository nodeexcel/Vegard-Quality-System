"""
SQS Processor - Send PDF processing jobs to SQS queue
"""
import boto3
import json
import logging
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)

class SQSProcessor:
    """Send PDF processing jobs to SQS for async processing"""
    
    def __init__(self):
        self.sqs = boto3.client('sqs', region_name='eu-north-1')
        self.queue_url = None
        self._get_or_create_queue()
    
    def _get_or_create_queue(self):
        """Get the SQS queue URL (queue should already exist)"""
        try:
            # Try to get queue URL (queue should already exist)
            response = self.sqs.get_queue_url(QueueName='validert-pdf-processing-queue')
            self.queue_url = response['QueueUrl']
            logger.info(f"Using SQS queue: {self.queue_url}")
        except Exception as e:
            # If queue doesn't exist or we don't have permission, log and set to None
            logger.warning(f"Could not get SQS queue URL: {str(e)}. Queue may need to be created manually.")
            self.queue_url = None
    
    def send_pdf_processing_job(
        self,
        s3_key: str,
        report_id: int,
        user_id: int,
        filename: str,
        report_system: Optional[str] = None,
        building_year: Optional[int] = None
    ) -> str:
        """
        Send PDF processing job to SQS
        
        Args:
            s3_key: S3 key where PDF is stored
            report_id: Database report ID
            user_id: User ID
            filename: Original filename
            report_system: Optional report system
            building_year: Optional building year
        
        Returns:
            SQS message ID
        """
        if not self.queue_url:
            raise Exception("SQS queue URL not available. Queue may need to be created or permissions configured.")
        
        try:
            message_body = {
                's3_key': s3_key,
                's3_bucket': settings.S3_BUCKET_NAME,
                'report_id': report_id,
                'user_id': user_id,
                'filename': filename,
                'report_system': report_system,
                'building_year': building_year
            }
            
            response = self.sqs.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(message_body),
                MessageAttributes={
                    'ReportId': {
                        'StringValue': str(report_id),
                        'DataType': 'Number'
                    },
                    'UserId': {
                        'StringValue': str(user_id),
                        'DataType': 'Number'
                    }
                }
            )
            
            message_id = response['MessageId']
            logger.info(f"Sent PDF processing job to SQS: {message_id}")
            return message_id
            
        except Exception as e:
            logger.error(f"Error sending to SQS: {str(e)}")
            raise
    
    def get_queue_stats(self):
        """Get queue statistics"""
        try:
            response = self.sqs.get_queue_attributes(
                QueueUrl=self.queue_url,
                AttributeNames=['All']
            )
            return response['Attributes']
        except Exception as e:
            logger.error(f"Error getting queue stats: {str(e)}")
            return {}

