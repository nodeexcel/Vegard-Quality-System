"""
S3 Storage Service - Store and retrieve PDFs from Amazon S3
"""
import boto3
from botocore.exceptions import ClientError
import logging
from typing import BinaryIO, Optional
from datetime import datetime
import os

logger = logging.getLogger(__name__)

class S3Storage:
    """Handle PDF storage in Amazon S3"""
    
    def __init__(self, bucket_name: str = "validert-reports"):
        self.s3_client = boto3.client('s3')
        self.bucket_name = bucket_name
        self._ensure_bucket_exists()
    
    def _ensure_bucket_exists(self):
        """Create S3 bucket if it doesn't exist"""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"S3 bucket '{self.bucket_name}' exists")
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                # Bucket doesn't exist, create it
                try:
                    # Create bucket in eu-north-1
                    self.s3_client.create_bucket(
                        Bucket=self.bucket_name,
                        CreateBucketConfiguration={
                            'LocationConstraint': 'eu-north-1'
                        }
                    )
                    logger.info(f"Created S3 bucket '{self.bucket_name}'")
                except Exception as create_error:
                    logger.error(f"Failed to create bucket: {str(create_error)}")
                    raise
            else:
                logger.error(f"Error checking bucket: {str(e)}")
                raise
    
    def upload_pdf(
        self, 
        file: BinaryIO, 
        filename: str, 
        user_id: int,
        report_id: int
    ) -> str:
        """
        Upload PDF to S3
        
        Args:
            file: File object
            filename: Original filename
            user_id: User ID
            report_id: Report ID
        
        Returns:
            S3 key (path) of uploaded file
        """
        try:
            # Create S3 key with structure: reports/{user_id}/{report_id}/{filename}
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            s3_key = f"reports/user_{user_id}/report_{report_id}/{timestamp}_{filename}"
            
            # Upload to S3
            file.seek(0)  # Reset file pointer
            self.s3_client.upload_fileobj(
                file,
                self.bucket_name,
                s3_key,
                ExtraArgs={
                    'ContentType': 'application/pdf',
                    'Metadata': {
                        'user_id': str(user_id),
                        'report_id': str(report_id),
                        'original_filename': filename.encode('ascii', 'ignore').decode('ascii') or 'report.pdf'
                    }
                }
            )
            
            logger.info(f"Uploaded PDF to S3: {s3_key}")
            return s3_key
            
        except Exception as e:
            logger.error(f"Error uploading to S3: {str(e)}")
            raise
    
    def download_pdf(self, s3_key: str) -> bytes:
        """
        Download PDF from S3
        
        Args:
            s3_key: S3 key (path) of the file
        
        Returns:
            PDF file content as bytes
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            return response['Body'].read()
            
        except Exception as e:
            logger.error(f"Error downloading from S3: {str(e)}")
            raise
    
    def get_presigned_url(self, s3_key: str, expiration: int = 3600) -> str:
        """
        Generate presigned URL for PDF download
        
        Args:
            s3_key: S3 key of the file
            expiration: URL expiration in seconds (default: 1 hour)
        
        Returns:
            Presigned URL
        """
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': s3_key
                },
                ExpiresIn=expiration
            )
            return url
            
        except Exception as e:
            logger.error(f"Error generating presigned URL: {str(e)}")
            raise
    
    def delete_pdf(self, s3_key: str):
        """Delete PDF from S3"""
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            logger.info(f"Deleted PDF from S3: {s3_key}")
            
        except Exception as e:
            logger.error(f"Error deleting from S3: {str(e)}")
            raise

