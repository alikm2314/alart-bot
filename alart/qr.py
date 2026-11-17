"""Branded, private subscription QR delivery."""
import io
import segno
from PIL import Image, ImageDraw, ImageFont
from fastapi import HTTPException

def provider_subscription_png(url:str):
    """Integer-scale QR with a complete quiet zone, even for long URLs."""
    if not url.startswith("https://") or len(url)>1800: raise HTTPException(422,"Invalid subscription URL")
    code=segno.make(url,error="m",micro=False)
    scale=max(3,min(10,800//code.symbol_size(border=4)[0]))
    output=io.BytesIO()
    code.save(output,kind="png",scale=scale,border=4,dark="#000000",light="#ffffff")
    return output.getvalue()

def subscription_png(url:str):
    if not url.startswith("https://") or len(url)>1800: raise HTTPException(422,"Invalid subscription URL")
    raw=io.BytesIO(); segno.make(url,error="m").save(raw,kind="png",scale=7,border=3,dark="#071a35",light="#ffffff")
    qr=Image.open(io.BytesIO(raw.getvalue())).convert("RGBA")
    canvas=Image.new("RGBA",(qr.width+96,qr.height+180),"#071426")
    draw=ImageDraw.Draw(canvas)
    draw.rounded_rectangle((18,18,canvas.width-18,canvas.height-18),radius=34,fill="#102846",outline="#2877e8",width=3)
    canvas.alpha_composite(qr,((canvas.width-qr.width)//2,54))
    font=ImageFont.load_default()
    draw.text((canvas.width//2,qr.height+88),"ALART  •  SUBSCRIPTION",anchor="ma",font=font,fill="#dbeaff")
    draw.text((canvas.width//2,qr.height+112),"Scan in your preferred client",anchor="ma",font=font,fill="#90a9c9")
    output=io.BytesIO(); canvas.convert("RGB").save(output,"PNG",optimize=True); return output.getvalue()

SCHEMES=("vless://","vmess://","trojan://","ss://","ssr://","hysteria2://","hy2://","tuic://","https://")
def first_connection_link(configuration:str):
    links=[line.strip() for line in configuration.splitlines() if line.strip()]
    if not links or not any(links[0].lower().startswith(scheme) for scheme in SCHEMES): raise HTTPException(422,"No supported connection link is available")
    if len(links[0].encode())>2900: raise HTTPException(422,"Connection link is too long for a QR code")
    return links[0]
def connection_png(configuration:str):
    link=first_connection_link(configuration); output=io.BytesIO()
    segno.make(link,error="m").save(output,kind="png",scale=5,border=5,dark="#0b1f3a",light="#f8fbff")
    return output.getvalue()
