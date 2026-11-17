"""Safe, fixed destinations for operator-created Telegram message buttons."""
from __future__ import annotations
import json
from .config import settings

def markup(buttons) -> dict | None:
    if isinstance(buttons,str):
        try: buttons=json.loads(buttons)
        except ValueError: return None
    if not isinstance(buttons,list): return None
    paths={
        'buy':'/app/?page=services',
        'services':'/app/?page=my-services',
        'miniapp':'/app/',
        'wallet':'/app/?topup=1',
        'guide':'/app/?page=guide', 'account':'/app/?page=account',
    }
    keyboard=[]
    for item in buttons[:3]:
        if not isinstance(item,dict): continue
        label=str(item.get('label') or '').strip()[:64]
        if not label: continue
        surface=item.get('surface','miniapp')
        action=item.get('action')
        style=item.get('style') if item.get('style') in {'primary','success','danger'} else 'primary'
        if surface=='chat' and action in {'buy','services','wallet','account','support'}:
            keyboard.append([{'text':label,'callback_data':f'menu:{action}','style':style}])
        elif surface=='miniapp' and action in paths:
            path=paths[action]
            coupon=str(item.get('coupon_code') or '').strip().upper()
            if action=='buy' and coupon:
                path+=('&' if '?' in path else '?')+'coupon='+coupon
            keyboard.append([{'text':label,'web_app':{'url':f"https://{settings()['DOMAIN']}{path}"},'style':style}])
    return {'inline_keyboard':keyboard} if keyboard else None
