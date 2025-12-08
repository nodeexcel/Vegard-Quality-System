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
        """Get or create the SQS queue"""
        try:
            # Try to get queue URL
            response = self.sqs.get_queue_url(QueueName='validert-pdf-processing-queue')
            self.queue_url = response['QueueUrl']
            logger.info(f"Using existing SQS queue: {self.queue_url}")
        except self.sqs.exceptions.QueueDoesNotExist:
            # Create queue if doesn't exist
            response = self.sqs.create_queue(
                QueueName='validert-pdf-processing-queue',
                Attributes={
                    'VisibilityTimeout': '900',  # 15 minutes
                    'MessageRetentionPeriod': '86400',  # 1 day
                    'ReceiveMessageWaitTimeSeconds': '20'  # Long polling
                }
            )
            self.queue_url = response['QueueUrl']
            logger.info(f"Created SQS queue: {self.queue_url}")
    
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

