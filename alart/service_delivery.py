import html
from datetime import datetime, timezone
from .service_state import effective_status
from .config import settings


def service_message(sub,language):
    fa=language=='fa'; sid=sub['id']; state=effective_status(sub)
    title=html.escape(str(sub.get('name') or sub.get('plan_name') or sid))
    name=html.escape(str(sub.get('client_name') or sid))
    text=(f'📡 <b>{title}</b>\n<code>{name}</code>\n──────────\n'
          f'این صفحه خلاصهٔ سرویس شماست. از دکمه‌های پایین برای تمدید، راهنمای اتصال یا برگشت به فهرست سرویس‌ها استفاده کنید.\n\n' if fa else
          f'📡 <b>{title}</b>\n<code>{name}</code>\n──────────\n'
          f'This is your service summary. Use the buttons below to renew, open connection guidance, or return to your services.\n\n')
    buttons=[]
    if state=='expired':
        text+=('🔴 <b>سرویس منقضی شده است</b>\nاعتبار زمانی یا حجم این سرویس تمام شده و اتصال آن دیگر کار نمی‌کند.\n\n<b>راه‌حل:</b> اگر این سرویس پولی است، «تمدید سرویس» را بزنید. اگر تست بوده، از «خرید سرویس» یک پلن جدید انتخاب کنید.' if fa else '🔴 <b>Service expired</b>\nIts time or traffic allowance has ended, so this connection no longer works.\n\n<b>Fix:</b> Use Renew service for a paid plan. If this was a trial, choose Buy service to create a new plan.')
    elif state not in ('active','expiring'):
        text+=('🟡 <b>سرویس فعلاً آماده نیست</b>\nممکن است ساخت سرویس هنوز در حال انجام باشد یا نیاز به بررسی داشته باشد. چند لحظه بعد دوباره «سرویس‌های من» را باز کنید؛ اگر وضعیت تغییر نکرد، با پشتیبانی پیام بدهید.' if fa else '🟡 <b>Service is not ready yet</b>\nIt may still be provisioning or need review. Open My services again in a moment; if the state does not change, contact Support.')
    else:
        expiry=sub.get('expires_at'); limit=sub.get('traffic_limit_bytes'); used=sub.get('traffic_used_bytes')
        hours=max(0,int((expiry-datetime.now(timezone.utc)).total_seconds()/3600)) if expiry else None
        duration=('بدون انقضا' if fa else 'No expiry') if hours is None else ((f'{hours//24} روز و {hours%24} ساعت' if fa else f'{hours//24} days {hours%24} hours') if hours>=24 else (f'{hours} ساعت' if fa else f'{hours} hours'))
        traffic=('نامحدود' if fa else 'Unlimited') if not limit else f'{limit/1024**3:g} GB'
        ips=sub.get('user_limit') or ('نامحدود' if fa else 'Unlimited')
        text+=(f'🟢 <b>سرویس فعال است</b>\n\n⏳ زمان باقی‌مانده: <b>{duration}</b>\n📶 سقف حجم: <b>{traffic}</b>\n👥 اتصال هم‌زمان: <b>{ips}</b>\n\n💡 لینک اشتراک را در اپ اتصال خود وارد کنید؛ برای آموزش مرحله‌به‌مرحله، دکمهٔ «آموزش اتصال» را بزنید.' if fa else f'🟢 <b>Service is active</b>\n\n⏳ Time remaining: <b>{duration}</b>\n📶 Traffic allowance: <b>{traffic}</b>\n👥 Concurrent IPs: <b>{ips}</b>\n\n💡 Import the subscription link in your connection app. Use Connection guide for step-by-step help.')
        if limit and used is not None:
            remaining=f'{max(0,limit-used)/1024**2:.0f} MB'
            text+=f'\nحجم باقی‌مانده: <b>{remaining}</b>' if fa else f'\nRemaining traffic: <b>{remaining}</b>'
        url=sub.get('provider_subscription_url')
        if url:
            escaped=html.escape(url,quote=True)
            text+=f'\n\n<a href="{escaped}">{"لینک اشتراک سنایی" if fa else "Sanaei subscription link"}</a>\n<code>{escaped}</code>'
            if len(url)<=256: buttons.append([{'text':'کپی لینک اشتراک' if fa else 'Copy subscription link','copy_text':{'text':url},'style':'success'}])
    if state in ('active','expiring','expired') and sub.get('price_minor',0)>0 and not sub.get('remote_missing_at'):
        buttons.append([{'text':'تمدید سرویس' if fa else 'Renew service','style':'success','web_app':{'url':f"https://{settings()['DOMAIN']}/app/?renew={sid}"}}])
    if state=='expired' and sub.get('price_minor',0)==0:
        buttons.append([{'text':'خرید سرویس' if fa else 'Buy service','callback_data':'menu:buy','style':'success'}])
    if state=='expired':
        buttons.append([{'text':'🗑 حذف این سرویس' if fa else '🗑 Delete this service','callback_data':f'service:archive:{sid}','style':'danger'}])
    else:
        buttons.append([{'text':'آموزش اتصال' if fa else 'Connection guide','web_app':{'url':f"https://{settings()['DOMAIN']}/app/?page=guide"},'style':'primary'}])
    buttons.append([{'text':'‹ بازگشت به سرویس‌های من' if fa else '‹ Back to My Services','callback_data':'menu:services','style':'danger'}])
    buttons.append([{'text':'‹ بازگشت به منوی اصلی' if fa else '‹ Back to Main Menu','callback_data':'menu:home','style':'danger'}])
    return text,{'inline_keyboard':buttons}
