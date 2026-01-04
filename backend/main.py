import logging
import os
import subprocess
import tempfile
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import yt_dlp

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="YT Downloader Backend")

# CORS - Allow all origins for the extension
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str
    cookies: Optional[str] = None # Netscape format cookies string

def create_cookie_file(cookies_content: str):
    """Creates a temporary cookie file and returns the path."""
    if not cookies_content:
        logger.warning("No cookies content provided!")
        return None
    try:
        logger.info(f"Creating cookie file with {len(cookies_content)} chars")
        # Create a temp file
        tf = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
        tf.write(cookies_content)
        tf.close()
        return tf.name
    except Exception as e:
        logger.error(f"Error creating cookie file: {e}")
        return None

@app.get("/")
def health_check():
    return {"status": "ok", "service": "YouTube Downloader Backend"}

@app.post("/info")
def get_video_info(request: VideoRequest):
    """
    Fetch metadata for a YouTube video.
    """
    url = request.url
    cookie_file = create_cookie_file(request.cookies)
    
    try:
        ydl_opts = {
            'quiet': True, 
            'no_warnings': True,
        }
        if cookie_file:
            ydl_opts['cookiefile'] = cookie_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            
            formats = []
            for f in info_dict.get('formats', []):
                if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                     formats.append({
                        'format_id': f['format_id'],
                        'ext': f['ext'],
                        'resolution': f.get('resolution', 'unknown'),
                        'filesize': f.get('filesize'),
                        'note': f.get('format_note')
                    })
            
            return {
                "title": info_dict.get('title'),
                "thumbnail": info_dict.get('thumbnail'),
                "formats": formats
            }
    except Exception as e:
        logger.error(f"Error extracting info: {e}")
        # Return generic error if cookie failed
        if "Sign in" in str(e):
             raise HTTPException(status_code=400, detail="YouTube requires authentication. Please ensure cookies are sent.")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        # Cleanup temp file
        if cookie_file and os.path.exists(cookie_file):
            os.unlink(cookie_file)

@app.post("/download") # Changed to POST to accept large cookie body easily
def download_video(request: VideoRequest, format_id: str = Query(None)):
    """
    Stream the video download directly to the client.
    """
    url = request.url
    cookies = request.cookies
    cookie_file = create_cookie_file(cookies)

    try:
        f_param = format_id if format_id else 'best'
        
        cmd = [
            "yt-dlp",
            "-f", f_param,
            "-o", "-",
            url
        ]
        
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
            # Note: We need to keep the file alive during the subprocess, 
            # but we also need to clean it up. 
            # For simplicity, we'll let it persist for the duration or rely on OS cleanup?
            # Better: defer cleanup or use a wrapper. 
            # Since this is a simple script, we'll accept a small leak or try to cleanup after process start?
            # Actually, yt-dlp reads the file at start. We can probably wait a bit or just accept the leak on free tier (it wipes on restart).
            pass

        # Open subprocess
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        def iterfile():
            try:
                while True:
                    data = proc.stdout.read(4096)
                    if not data:
                        break
                    yield data
                proc.stdout.close()
            except Exception as e:
                logger.error(f"Streaming error: {e}")
                proc.kill()
            finally:
                if cookie_file and os.path.exists(cookie_file):
                    # Attempt cleanup after streaming
                    try:
                        os.unlink(cookie_file)
                    except:
                        pass

        headers = {
            "Content-Disposition": f'attachment; filename="video.mp4"' 
        }
        
        return StreamingResponse(iterfile(), media_type="video/mp4", headers=headers)

    except Exception as e:
        logger.error(f"Download error: {e}")
        if cookie_file and os.path.exists(cookie_file):
            os.unlink(cookie_file)
        raise HTTPException(status_code=500, detail="Download failed")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
