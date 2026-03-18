import os
import uuid
from google.cloud import storage

def upload_content_to_gcs(content: bytes, original_filename: str, content_type: str, category: str) -> str:
    """
    Synchronously uploads content to Google Cloud Storage and returns the public URL.
    """
    try:
        bucket_name = os.getenv("GCS_BUCKET_NAME", "workspehere-bukcet")
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        # Generate a unique filename using UUID
        ext = ""
        if original_filename and "." in original_filename:
            ext = "." + original_filename.split(".")[-1]
            
        unique_filename = f"{uuid.uuid4().hex}{ext}"
        
        # Path in the bucket (e.g. "uploads/profile/xxxxx.png")
        blob_path = f"uploads/{category}/{unique_filename}"
        blob = bucket.blob(blob_path)

        # Upload the file
        blob.upload_from_string(content, content_type=content_type)
        
        # Return the public URL
        return f"https://storage.googleapis.com/{bucket_name}/{blob_path}"
    except Exception as e:
        print(f"Error uploading to GCS: {e}")
        return ""
