"""Owner-managed bilingual guides and bounded, streamed video uploads."""
import os
import re
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .auth import current_user, require_owner, csrf
from .db import transaction

router=APIRouter()
VIDEO_KEY=re.compile(r'^[0-9a-f]{32}\.(mp4|webm)$')
MAX_VIDEO=50*1024*1024

def directory():
    return Path(os.environ.get('PRIVATE_DATA_DIR','/srv/data'))/'guides'

class Guide(BaseModel):
    platform:str=Field(pattern=r'^(ios|android|windows|macos)$')
    language:str=Field(pattern=r'^(fa|en)$')
    title:str=Field(min_length=1,max_length=120)
    body:str=Field(max_length=12000)
    video_key:str|None=None
    published:bool=True

@router.get('/api/guides')
def guides(user=Depends(current_user)):
    with transaction() as conn:
        return conn.execute('SELECT platform,title,body,video_key FROM connection_guides WHERE language=%s AND published ORDER BY platform',(user['language'],)).fetchall()

@router.get('/api/admin/guides')
def admin_guides(owner=Depends(require_owner)):
    with transaction() as conn:
        return conn.execute('SELECT * FROM connection_guides ORDER BY platform,language').fetchall()

@router.put('/api/admin/guides')
def save_guide(body:Guide,request:Request,owner=Depends(require_owner)):
    csrf(request)
    if body.video_key and (not VIDEO_KEY.fullmatch(body.video_key) or not (directory()/body.video_key).is_file()):
        raise HTTPException(422,'Video not found')
    with transaction() as conn:
        conn.execute('''INSERT INTO connection_guides(platform,language,title,body,video_key,published)
          VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (platform,language) DO UPDATE
          SET title=EXCLUDED.title,body=EXCLUDED.body,video_key=EXCLUDED.video_key,published=EXCLUDED.published,updated_at=now()''',
          (body.platform,body.language,body.title,body.body,body.video_key,body.published))
    return {'ok':True}

@router.put('/api/admin/guides/video')
async def upload_video(request:Request,owner=Depends(require_owner)):
    csrf(request)
    mime=request.headers.get('content-type','').split(';')[0]
    if mime not in ('video/mp4','video/webm'): raise HTTPException(422,'Upload MP4 or WebM video')
    root=directory(); root.mkdir(parents=True,exist_ok=True)
    key=uuid.uuid4().hex+('.mp4' if mime=='video/mp4' else '.webm')
    path=root/key
    try:
        size=0; header=b''
        with path.open('xb') as output:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>MAX_VIDEO: raise HTTPException(413,'Video must be under 50 MB')
                if len(header)<16: header=(header+chunk)[:16]
                output.write(chunk)
        if size<16 or (mime=='video/mp4' and header[4:8]!=b'ftyp') or (mime=='video/webm' and header[:4]!=b'\x1aE\xdf\xa3'):
            raise HTTPException(422,'Invalid video file')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {'video_key':key}

@router.get('/api/guides/video/{key}')
def video(key:str,user=Depends(current_user)):
    if not VIDEO_KEY.fullmatch(key): raise HTTPException(404,'Video not found')
    with transaction() as conn:
        found=conn.execute('SELECT 1 FROM connection_guides WHERE video_key=%s AND published',(key,)).fetchone()
    if not found or not (directory()/key).is_file(): raise HTTPException(404,'Video not found')
    return FileResponse(directory()/key,media_type='video/mp4' if key.endswith('.mp4') else 'video/webm',headers={'Cache-Control':'private, max-age=3600'})
