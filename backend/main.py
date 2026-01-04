import logging
import os
import subprocess
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str

@app.get("/")
def health_check():
    return {"status": "ok", "service": "YouTube Downloader Backend"}

@app.post("/info")
def get_video_info(request: VideoRequest):
    """
    Fetch metadata for a YouTube video.
    Returns title, thumbnail, and available formats.
    """
    url = request.url
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            
            # Extract relevant formats (simplify for UI)
            formats = []
            for f in info_dict.get('formats', []):
                # Filter for useful formats (mp4 with video+audio is rare in high quality, 
                # so we might list 'best' or specialized ones. 
                # For simplicity, let's just grab a few distinct resolution mp4s with audio if possible, 
                # or rely on 'best' flag in download).
                
                # Check for video+audio combined (filesize often present)
                if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                     formats.append({
                        'format_id': f['format_id'],
                        'ext': f['ext'],
                        'resolution': f.get('resolution', 'unknown'),
                        'filesize': f.get('filesize'),
                        'note': f.get('format_note')
                    })
            
            # Reverse sort by resolution/quality if possible, or just send best.
            # If no combined formats, user might settle for 'best' (handled by download logic).
            
            return {
                "title": info_dict.get('title'),
                "thumbnail": info_dict.get('thumbnail'),
                "duration": info_dict.get('duration'),
                "formats": formats
            }
    except Exception as e:
        logger.error(f"Error extracting info: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/download")
def download_video(url: str = Query(..., description="YouTube Video URL"), 
                   format_id: Optional[str] = Query(None, description="Specific format ID or 'best'")):
    """
    Stream the video download directly to the client.
    Uses yt-dlp to pipe stdout.
    """
    try:
        # Determine format. If None, default to 'best' (single file, video+audio).
        # Note: 'best' in yt-dlp usually means best video+audio combined.
        # If we want 1080p, we often need to merge video+audio, which requires ffmpeg.
        # On Render Free Tier, ffmpeg might not be easy or fast enough. 
        # So we prefer 'best' (often 720p) or explicit format IDs that are pre-merged.
        
        f_param = format_id if format_id else 'best'
        
        # Command to stream to stdout
        # -o - : output to stdout
        cmd = [
            "yt-dlp",
            "-f", f_param,
            "-o", "-",
            url
        ]
        
        # Open subprocess
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Generator to yield chunks
        def iterfile():
            try:
                while True:
                    data = proc.stdout.read(4096)
                    if not data:
                        break
                    yield data
                proc.stdout.close()
                return_code = proc.wait()
                if return_code != 0:
                     # Check stderr if needed, but stream already started
                     pass
            except Exception as e:
                logger.error(f"Streaming error: {e}")
                proc.kill()

        # Set headers for download
        headers = {
            "Content-Disposition": f'attachment; filename="video.mp4"' 
            # Ideally we'd know the filename/ext ahead of time, but for streaming pipes it's tricky.
            # We can default to video.mp4 or try to guess.
        }
        
        return StreamingResponse(iterfile(), media_type="video/mp4", headers=headers)

    except Exception as e:
        logger.error(f"Download error: {e}")
        raise HTTPException(status_code=500, detail="Download failed")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
