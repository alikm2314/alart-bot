import logging
import json
import hashlib
import secrets
import html
import httpx
from .config import settings
from .db import transaction
from .core import settle_order
from .payment_integrations import TelegramStars, stars_precheckout_matches
from .money import toman
from .localization import tr, localized_date
from .referral import claim as claim_referral, ensure_code as ensure_referral_code, referral_link

log=logging.getLogger("alart.bot")
TERMS_VERSION="1"
LABELS={
    "en":{"buy":"🛒 Buy Service","services":"📦 My Services","wallet":"💳 Wallet","account":"👤 My Account","refer":"🎁 Refer Friends","support":"💬 Support","admin":"⚙️ Management","status":"📊 System Status","topics":"🧵 Set up report group","home":"↩ Back to main menu"},
    "fa":{"buy":"🛒 خرید سرویس","services":"📦 سرویس‌های من","wallet":"💳 کیف پول","account":"👤 حساب من","refer":"🎁 معرفی دوستان","support":"💬 پشتیبانی","admin":"⚙️ مدیریت","status":"📊 وضعیت سیستم","topics":"🧵 راه‌اندازی گروه گزارش","home":"↩ بازگشت به منوی اصلی"},
}

def api(method,payload):
    markup=payload.get('reply_markup') or {}
    for row in markup.get('inline_keyboard',[]):
        for button in row:
            action=button.get('callback_data','')
            button.setdefault('style','success' if action.startswith(('buy:pay','buy:trial:')) or 'copy_text' in button else 'danger' if action in ('menu:home','topup:cancel','menu:support') else 'primary')
    with httpx.Client(timeout=35) as client:
        r=client.post(f"https://api.telegram.org/bot{settings()['BOT_TOKEN']}/{method}",json=payload)
        r.raise_for_status()
        data=r.json()
    if not data.get("ok"):
        raise RuntimeError("Telegram request failed")
    return data["result"]

def message(chat_id,text,reply_markup=None,html=False):
    payload={"chat_id":chat_id,"text":text[:4000]}
    if html: payload["parse_mode"]="HTML"
    payload['link_preview_options']={'is_disabled':True}
    if reply_markup: payload["reply_markup"]=reply_markup
    api("sendMessage",payload)

def user_error(language, title, reason, next_step):
    """A consistent, actionable customer-facing error card for chat."""
    if language=='fa':
        return f"⚠️ <b>{title}</b>\n──────────\n{reason}\n\n<b>چه کار کنید؟</b>\n{next_step}\n\nاگر مشکل ادامه داشت، از دکمهٔ «پشتیبانی» پیام بدهید."
    return f"⚠️ <b>{title}</b>\n──────────\n{reason}\n\n<b>What to do next</b>\n{next_step}\n\nIf it continues, use the Support button and tell us what you were trying to do."

def edit_message(chat_id,message_id,text,reply_markup=None,html=False):
    """Edit only bot-owned private-chat navigation messages."""
    payload={"chat_id":chat_id,"message_id":message_id,"text":text[:4000]}
    if html: payload["parse_mode"]="HTML"
    if reply_markup: payload["reply_markup"]=reply_markup
    api("editMessageText",payload)

def delete_message(chat_id,message_id):
    api("deleteMessage",{"chat_id":chat_id,"message_id":message_id})

def renewal_button(action_path,language):
    import re
    if action_path=='/app/?page=notifications':
        return {'inline_keyboard':[[{'text':'مشاهده جزئیات' if language=='fa' else 'View details','style':'primary','web_app':{'url':f"https://{settings()['DOMAIN']}{action_path}"}}]]}
    if not re.fullmatch(r'/app/\?renew=[0-9]+',action_path):
        raise ValueError('Invalid renewal action')
    return {'inline_keyboard':[[{'text':'🔄 تمدید سرویس' if language=='fa' else '🔄 Renew service',
        'style':'success','web_app':{'url':f"https://{settings()['DOMAIN']}{action_path}"}}]]}

def keyboard(language,admin=False):
    labels=LABELS[language]
    # Customers navigate through inline controls so their compose keyboard stays
    # empty.  Owners keep just two operational shortcuts in their keyboard.
    if not admin: return {"remove_keyboard":True}
    return {"keyboard":[[labels["admin"],labels["status"]],[labels["topics"],labels["home"]]],"resize_keyboard":True,"is_persistent":True}

def main_menu(language):
    labels=LABELS[language]
    return {"inline_keyboard":[
        [{"text":labels["buy"],"callback_data":"menu:buy"}],
        [{"text":labels["services"],"callback_data":"menu:services"},{"text":labels["wallet"],"callback_data":"menu:wallet"}],
        [{"text":labels["account"],"callback_data":"menu:account"},{"text":labels["refer"],"callback_data":"menu:refer"}],
        [{"text":labels["support"],"callback_data":"menu:support"}],
    ]}

def main_menu_text(language):
    return ("✦ Welcome to Alart\n──────────\nYour connection, purchases, wallet, and account live here. Start with Buy Service for a new configuration, or open My Services to manage an existing one.\n\n💡 Every step explains what happens next, and your connection details remain private and secure. Choose an option below to continue."
            if language=="en" else
            "✦ به آلارت خوش آمدید\n──────────\nاینجا مرکز مدیریت سرویس، خریدها، کیف پول و حساب شماست. برای کانفیگ جدید «خرید سرویس» را بزنید؛ برای دیدن یا تمدید کانفیگ قبلی، «سرویس‌های من» را انتخاب کنید.\n\n💡 در هر مرحله توضیح می‌دهیم چه اتفاقی می‌افتد و اطلاعات اتصال فقط برای خود شما نمایش داده می‌شود. از گزینه‌های زیر شروع کنید.")

def forwarded_group(message_data):
    """Return the original group chat from a Telegram forwarded message.

    Bot API represents forwarded group messages as ``sender_chat`` for the
    ``chat`` forward-origin type, while older updates used
    ``forward_from_chat``.  Treating those formats separately had caused the
    setup flow to fall through to the ordinary customer menu.
    """
    origin=message_data.get('forward_origin') or {}
    candidate=origin.get('chat') or origin.get('sender_chat') or message_data.get('forward_from_chat') or {}
    return candidate if isinstance(candidate,dict) else {}

def back_menu(language, *, wallet=False):
    back="‹ Back to main menu" if language=="en" else "بازگشت به منوی اصلی ›"
    rows=[]
    if wallet:
        rows.append([{"text":"➕ Top up wallet" if language=="en" else "➕ شارژ کیف پول","callback_data":"menu:topup"}])
    rows.append([{"text":back,"callback_data":"menu:home"}])
    return {"inline_keyboard":rows}

def _auto_location(conn, service_id):
    """Choose a configured capacity location; it is operational data, not UX."""
    row=conn.execute("""SELECT l.id FROM locations l WHERE l.enabled AND EXISTS (
      SELECT 1 FROM servers s JOIN managed_panels p ON p.id=s.panel_id
      WHERE s.location_id=l.id AND s.status='enabled' AND p.enabled AND p.health='online'
      AND EXISTS (SELECT 1 FROM panel_inbounds i JOIN service_inbound_mappings m ON m.inbound_id=i.id
        WHERE i.panel_id=p.id AND i.allowed AND i.remote_enabled AND m.service_id=%s)
    ) ORDER BY l.id LIMIT 1""",(service_id,)).fetchone()
    return row["id"] if row else None

def setup_topics(chat_id: int):
    """Create the standard private operational topics in one enabled forum.

    Telegram is the authority for membership and forum status: getChat fails if
    the bot cannot access the requested group.  Topic identifiers are persisted
    only after Telegram confirms every create operation.
    """
    from .ops_topics import TOPIC_TITLES
    chat=api("getChat",{"chat_id":chat_id})
    if not chat.get("is_forum"):
        raise ValueError("The selected group must have Topics enabled")
    created={}
    with transaction() as conn:
        existing={row['category']:row['topic_id'] for row in conn.execute("SELECT category,topic_id FROM ops_topic_settings WHERE chat_id=%s",(chat_id,)).fetchall()}
    if existing:
        # Re-running setup is a safe rename operation for an already managed
        # group. It never creates duplicate topic sets.
        for category,title in TOPIC_TITLES.items():
            if category in existing:
                api("editForumTopic",{"chat_id":chat_id,"message_thread_id":existing[category],"name":title})
        return existing
    for category,title in TOPIC_TITLES.items():
        result=api("createForumTopic",{"chat_id":chat_id,"name":title})
        thread_id=result.get("message_thread_id")
        if type(thread_id) is not int or thread_id<1:
            raise RuntimeError("Telegram did not return a topic identifier")
        created[category]=thread_id
    with transaction() as conn:
        for category,thread_id in created.items():
            conn.execute("""INSERT INTO ops_topic_settings(category,chat_id,topic_id)
              VALUES (%s,%s,%s) ON CONFLICT (category) DO UPDATE SET chat_id=EXCLUDED.chat_id,topic_id=EXCLUDED.topic_id""",(category,chat_id,thread_id))
    return created

def onboarding(chat_id,language=None):
    if language is None:
        message(chat_id,"🌍 <b>Welcome to Alart / به آلارت خوش آمدید</b>\n──────────\nبرای اینکه همهٔ پیام‌ها، راهنماها و مراحل خرید با زبان شما نمایش داده شوند، ابتدا زبان دلخواهتان را انتخاب کنید.\n\nChoose your preferred language to continue.",{"inline_keyboard":[[{"text":"فارسی","callback_data":"lang:fa"},{"text":"English","callback_data":"lang:en"}]]},html=True)
    else:
        try:
            with transaction() as conn:
                row=conn.execute("SELECT fa,en FROM bot_content WHERE content_key='terms'").fetchone()
        except RuntimeError: # permits isolated bot presentation tests without database configuration
            row=None
        text=(row['fa'] if language=='fa' else row['en']) if row else ("شرایط استفاده نسخه ۱: از این سرویس مطابق قوانین استفاده کنید. اطلاعات اتصال خود را محرمانه نگه دارید. خرید و بازپرداخت طبق شرایط اعلام‌شده در زمان سفارش انجام می‌شود. برای دریافت خدمات، پذیرش این شرایط لازم است." if language=='fa' else "Terms v1: Use this service lawfully and keep your connection details private. Purchases and refunds follow the terms shown at checkout. You must accept these terms to use Alart.")
        intro=("📄 <b>شرایط استفاده از آلارت</b>\n──────────\nقبل از شروع، متن زیر را با دقت بخوانید. پذیرش آن برای ساخت سرویس، پرداخت و دریافت راهنمای اتصال لازم است.\n\n" if language=='fa' else "📄 <b>Alart terms of use</b>\n──────────\nPlease read the terms below before you start. Acceptance is required to create services, make payments, and receive connection guidance.\n\n")
        message(chat_id,intro+text,{"inline_keyboard":[[{"text":"✓ شرایط را می‌پذیرم" if language=="fa" else "✓ I accept the terms","callback_data":f"terms:{TERMS_VERSION}","style":"success"}]]},html=True)

def handle(update):
    refund_message=update.get('message') or {}
    refunded=refund_message.get('refunded_payment')
    if refunded:
        payload=refunded.get('invoice_payload','')
        charge=refunded.get('telegram_payment_charge_id')
        with transaction() as conn:
            row=conn.execute("""SELECT p.*,u.telegram_id FROM payments p JOIN orders o ON o.id=p.order_id
              JOIN users u ON u.id=o.user_id WHERE p.provider='stars' AND p.provider_reference=%s FOR UPDATE OF o,p""",(charge,)).fetchone()
            if (not charge or not row or row['invoice_payload']!=payload or refunded.get('currency')!='XTR'
                or refunded.get('total_amount')!=row['amount_minor'] or refund_message.get('chat',{}).get('id')!=row['telegram_id']):
                raise ValueError('Telegram refund did not match the payment')
            from .refunds import provider_refunded
            provider_refunded(conn,row['order_id'],'telegram',charge)
        return
    if update.get("pre_checkout_query"):
        query=update["pre_checkout_query"]
        payload=query.get("invoice_payload","")
        if payload.startswith("alart:topup:") and payload.rsplit(":",1)[-1].isdigit():
            with transaction() as conn:
                topup=conn.execute("SELECT w.status,w.invoice_payload,w.amount_minor,w.currency,u.telegram_id,u.language FROM wallet_topups w JOIN users u ON u.id=w.user_id WHERE w.id=%s AND w.provider='stars'",(int(payload.rsplit(":",1)[-1]),)).fetchone()
                accepted=bool(topup and topup['status']=='awaiting_payment' and topup['invoice_payload']==payload and topup['currency']=='XTR' and query.get('currency')=='XTR' and query.get('total_amount')==topup['amount_minor'] and (query.get('from') or {}).get('id')==topup['telegram_id'])
            TelegramStars().answer_precheckout(query['id'],accepted,language=topup['language'] if topup else 'en'); return
        order_id=int(payload.rsplit(":",1)[-1]) if payload.startswith("alart:order:") and payload.rsplit(":",1)[-1].isdigit() else 0
        accepted=False
        if order_id:
            with transaction() as conn:
                row=conn.execute("SELECT o.status,u.telegram_id,u.language,p.invoice_payload,p.amount_minor,p.currency FROM orders o JOIN users u ON u.id=o.user_id JOIN payments p ON p.order_id=o.id WHERE o.id=%s AND p.provider='stars'",(order_id,)).fetchone()
                accepted=bool(row and stars_precheckout_matches(query,row,row["telegram_id"],row["status"]))
        TelegramStars().answer_precheckout(query["id"],accepted,language=(row or {}).get('language') if order_id else 'en')
        return
    item=update.get("message") or {}
    if item.get("successful_payment"):
        paid=item["successful_payment"]
        payload=paid.get("invoice_payload","")
        if payload.startswith("alart:topup:") and payload.rsplit(":",1)[-1].isdigit():
            charge=str(paid.get('telegram_payment_charge_id') or '')
            with transaction() as conn:
                row=conn.execute("SELECT w.*,u.telegram_id,u.language FROM wallet_topups w JOIN users u ON u.id=w.user_id WHERE w.id=%s AND w.provider='stars' FOR UPDATE",(int(payload.rsplit(":",1)[-1]),)).fetchone()
                if not row or not charge or row['status']!='awaiting_payment' or row['invoice_payload']!=payload or row['currency']!='XTR' or paid.get('currency')!='XTR' or paid.get('total_amount')!=row['amount_minor'] or item.get('from',{}).get('id')!=row['telegram_id']: raise ValueError('Telegram wallet payment did not match')
                from .core import ledger
                ledger(conn,row['user_id'],row['amount_minor_irr'],'deposit',f"stars-topup:{row['id']}")
                conn.execute("UPDATE wallet_topups SET status='approved',provider_reference=%s,approved_amount_minor=amount_minor_irr,reviewed_at=now(),updated_at=now() WHERE id=%s",(charge,row['id']))
            message(item['chat']['id'],('✅ <b>Wallet top-up confirmed</b>\n──────────\nYour balance has been updated and is ready to use for an eligible purchase. Open Wallet any time to review the transaction.' if row['language']=='en' else '✅ <b>شارژ کیف پول تأیید شد</b>\n──────────\nموجودی شما افزایش پیدا کرد و برای خریدهای مجاز قابل استفاده است. هر زمان خواستید از بخش کیف پول جزئیات تراکنش را ببینید.'),html=True); return
        order_id=int(payload.rsplit(":",1)[-1]) if payload.startswith("alart:order:") and payload.rsplit(":",1)[-1].isdigit() else 0
        charge=str(paid.get("telegram_payment_charge_id") or "")
        if not order_id or not charge:
            return
        with transaction() as conn:
            row=conn.execute("SELECT p.*,o.user_id,u.telegram_id,u.language FROM payments p JOIN orders o ON o.id=p.order_id JOIN users u ON u.id=o.user_id WHERE p.order_id=%s AND p.provider='stars' FOR UPDATE OF p,o",(order_id,)).fetchone()
            if not row or row["invoice_payload"]!=payload or row["currency"]!="XTR" or paid.get("currency")!="XTR" or paid.get("total_amount")!=row["amount_minor"] or item.get("from",{}).get("id")!=row["telegram_id"]:
                raise ValueError("Telegram payment did not match the order")
            if row["provider_reference"] and row["provider_reference"]!=charge:
                raise ValueError("Second Telegram charge for an already settled order")
            conn.execute("INSERT INTO payment_events(payment_id,event_key,provider_status) VALUES (%s,%s,'confirmed') ON CONFLICT DO NOTHING",(row["id"],"stars:"+charge))
            settled=settle_order(conn,order_id,provider="stars",reference=charge,amount=row["amount_minor"],currency="XTR")
        message(item["chat"]["id"],('⚠️ <b>Payment received after cancellation</b>\n──────────\nThe payment was confirmed after the order was cancelled. A refund is now being processed; please do not create a duplicate order.' if settled['status']=='cancelled' and row['language']=='en' else '⚠️ <b>پرداخت پس از لغو تأیید شد</b>\n──────────\nپرداخت بعد از لغو سفارش تأیید شده است. بازپرداخت در حال انجام است؛ لطفاً سفارش تکراری نسازید.' if settled['status']=='cancelled' else '✅ <b>Payment received</b>\n──────────\nYour payment is confirmed and your service is now being created. We will send the subscription link and QR code as soon as it is ready.' if row['language']=='en' else '✅ <b>پرداخت تأیید شد</b>\n──────────\nپرداخت شما ثبت شد و ساخت سرویس آغاز شده است. به‌محض آماده‌شدن، لینک اشتراک و QR کد برایتان ارسال می‌شود.'),html=True)
        return
    callback=update.get("callback_query")
    if callback:
        person=callback.get("from") or {}
        chat=(callback.get("message") or {}).get("chat") or {}
        user_id=person.get("id")
        if not user_id or chat.get("type")!="private" or chat.get("id")!=user_id:
            api("answerCallbackQuery",{"callback_query_id":callback["id"]})
            return
        action=callback.get("data") or ""
        if action.startswith("admin:topup:"):
            parts=action.split(":")
            with transaction() as conn:
                admin=conn.execute("SELECT u.id FROM admins a JOIN users u ON u.id=a.user_id WHERE u.telegram_id=%s AND a.role IN ('owner','operator')",(user_id,)).fetchone()
                row=conn.execute("SELECT w.*,u.telegram_id,u.language FROM wallet_topups w JOIN users u ON u.id=w.user_id WHERE w.id=%s FOR UPDATE",(int(parts[2]) if len(parts)==4 and parts[2].isdigit() else 0,)).fetchone()
                if not admin or not row or row['status'] not in ('manual_review','more_info'):
                    reply="This payment is no longer awaiting review."; markup=None
                elif parts[3]=='adjust':
                    conn.execute("INSERT INTO bot_payment_states(user_id,stage,entity_id) VALUES (%s,'admin_adjust',%s) ON CONFLICT (user_id) DO UPDATE SET stage='admin_adjust',entity_id=EXCLUDED.entity_id,updated_at=now()",(admin['id'],row['id']))
                    reply="Send the verified amount in Toman. The top-up will remain pending until you press Approve."; markup=None
                elif parts[3] in ('approve','reject','more_info'):
                    if parts[3]=='approve':
                        from .core import ledger
                        ledger(conn,row['user_id'],row['amount_minor'],'deposit',f"wallet-topup:{row['id']}")
                        status='approved'; note='Your wallet top-up was approved.'
                    elif parts[3]=='reject': status='rejected'; note='Your wallet top-up was rejected.'
                    else: status='more_info'; note='More information is needed for your wallet top-up.'
                    conn.execute("UPDATE wallet_topups SET status=%s,approved_amount_minor=CASE WHEN %s='approved' THEN amount_minor ELSE approved_amount_minor END,reviewed_by=%s,reviewed_at=now(),updated_at=now() WHERE id=%s",(status,status,admin['id'],row['id']))
                    conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(row['user_id'],note))
                    conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,%s,'wallet_topup',%s)",(admin['id'],'wallet_topup.'+parts[3],row['id']))
                    reply=f"Top-up #{row['id']} marked {status}."; markup=None
                else: reply='Unsupported review action.'; markup=None
            api("answerCallbackQuery",{"callback_query_id":callback["id"]})
            message(chat['id'],reply,markup)
            if admin and row and len(parts)==4 and parts[3] in ('approve','reject','more_info'):
                customer_text={'approve':('Your wallet top-up was approved and your balance was updated.' if row['language']=='en' else 'شارژ کیف پول شما تأیید شد و موجودی حساب افزایش پیدا کرد.'),'reject':('Your wallet top-up was rejected. Please contact support if you need details.' if row['language']=='en' else 'شارژ کیف پول شما تأیید نشد. اگر به جزئیات نیاز دارید با پشتیبانی تماس بگیرید.'),'more_info':('More information is needed for your wallet top-up. Please contact support.' if row['language']=='en' else 'برای بررسی شارژ کیف پول شما اطلاعات بیشتری لازم است. با پشتیبانی تماس بگیرید.')}[parts[3]]
                try: message(row['telegram_id'],customer_text,back_menu(row['language'],wallet=True))
                except Exception: log.exception('Could not immediately notify top-up customer')
            return
        # Manual card-transfer controls stay in Telegram; no customer is redirected to the Mini App.
        if action.startswith("topup:"):
            with transaction() as conn:
                found=conn.execute("SELECT id,language FROM users WHERE telegram_id=%s",(user_id,)).fetchone()
                if not found: return
                if action.startswith("topup:method:") and action.rsplit(':',1)[-1] in ('manual','stars','nowpayments'):
                    provider=action.rsplit(':',1)[-1]
                    if not conn.execute("SELECT 1 FROM payment_settings WHERE provider=%s AND enabled",(provider,)).fetchone():
                        reply=user_error(found['language'],'Payment method unavailable' if found['language']=='en' else 'این روش پرداخت در دسترس نیست','This payment method is disabled or temporarily unavailable.' if found['language']=='en' else 'این روش پرداخت غیرفعال است یا موقتاً در دسترس نیست.','Return to Wallet and choose another method.' if found['language']=='en' else 'به کیف پول برگردید و روش دیگری را انتخاب کنید.'); markup=back_menu(found['language'],wallet=True)
                    elif provider=='nowpayments':
                        reply="Crypto wallet top-up is being prepared. Please use Card transfer or Telegram Stars for now." if found['language']=='en' else "شارژ رمزارزی در حال آماده‌سازی است. فعلاً از کارت‌به‌کارت یا استارز تلگرام استفاده کنید."; markup=back_menu(found['language'],wallet=True)
                    else:
                        conn.execute("INSERT INTO bot_payment_states(user_id,stage,provider) VALUES (%s,'topup_amount',%s) ON CONFLICT (user_id) DO UPDATE SET stage='topup_amount',provider=EXCLUDED.provider,entity_id=NULL,updated_at=now()",(found['id'],provider))
                        reply=("💳 <b>Choose your top-up amount</b>\n──────────\nSend the amount you want to add in Toman. You can enter any whole amount from <b>1,000</b> to <b>1,000,000,000 Toman</b>.\n\nFor example: <code>100000</code>\n\nBefore you pay, we will show the exact destination and amount for you to review." if found['language']=='en' else "💳 <b>مبلغ شارژ کیف پول را انتخاب کنید</b>\n──────────\nمبلغی را که می‌خواهید به تومان اضافه کنید بفرستید. مبلغ باید یک عدد صحیح بین <b>۱٬۰۰۰</b> تا <b>۱٬۰۰۰٬۰۰۰٬۰۰۰ تومان</b> باشد.\n\nمثال: <code>100000</code>\n\nپیش از پرداخت، مقصد و مبلغ دقیق را برای بررسی به شما نشان می‌دهیم.")
                        markup={"inline_keyboard":[[{"text":"Cancel" if found['language']=='en' else "لغو","callback_data":"topup:cancel"}] ]}
                elif action=="topup:cancel":
                    conn.execute("DELETE FROM bot_payment_states WHERE user_id=%s",(found['id'],))
                    reply=("↩️ <b>Top-up cancelled</b>\n──────────\nNo payment was created and your wallet balance has not changed. You can start a new top-up whenever you are ready." if found['language']=='en' else "↩️ <b>شارژ کیف پول لغو شد</b>\n──────────\nهیچ پرداختی ساخته نشده و موجودی کیف پول شما تغییری نکرده است. هر زمان آماده بودید می‌توانید دوباره شارژ را شروع کنید.")
                    markup=back_menu(found['language'],wallet=True)
                elif action=="topup:receipt":
                    state=conn.execute("SELECT entity_id FROM bot_payment_states WHERE user_id=%s AND stage='topup_receipt'",(found['id'],)).fetchone()
                    if not state: reply=user_error(found['language'],'Request expired' if found['language']=='en' else 'این درخواست منقضی شده است','The previous top-up step is no longer active.' if found['language']=='en' else 'مرحلهٔ قبلی شارژ دیگر فعال نیست.','Start a new top-up from Wallet.' if found['language']=='en' else 'از بخش کیف پول یک شارژ جدید شروع کنید.'); markup=back_menu(found['language'])
                    else:
                        conn.execute("UPDATE bot_payment_states SET stage='topup_photo',updated_at=now() WHERE user_id=%s",(found['id'],))
                        reply=("🧾 <b>Send your payment receipt</b>\n──────────\nUpload a clear screenshot or photo of the transfer receipt. Accepted formats are JPEG, PNG, and WebP, up to <b>3 MB</b>.\n\nMake sure the amount, destination, and transaction details are visible. Our team reviews it before your wallet balance is credited." if found['language']=='en' else "🧾 <b>رسید پرداخت را ارسال کنید</b>\n──────────\nیک تصویر واضح از رسید یا اسکرین‌شات انتقال بفرستید. فرمت‌های JPEG، PNG و WebP تا سقف <b>۳ مگابایت</b> پذیرفته می‌شوند.\n\nمبلغ، مقصد و جزئیات تراکنش باید در رسید خوانا باشند. پس از بررسی، موجودی کیف پول شما افزایش پیدا می‌کند.")
                        markup={"inline_keyboard":[[{"text":"Cancel" if found['language']=='en' else "لغو","callback_data":"topup:cancel"}]]}
                elif action=="topup:change":
                    conn.execute("UPDATE bot_payment_states SET stage='topup_amount',entity_id=NULL,updated_at=now() WHERE user_id=%s",(found['id'],))
                    reply=("✏️ <b>Enter the new amount</b>\n──────────\nSend a whole amount between <b>1,000</b> and <b>1,000,000,000 Toman</b>. We will create a new payment instruction; do not transfer the previous amount." if found['language']=='en' else "✏️ <b>مبلغ جدید را وارد کنید</b>\n──────────\nیک مبلغ صحیح بین <b>۱٬۰۰۰</b> تا <b>۱٬۰۰۰٬۰۰۰٬۰۰۰ تومان</b> بفرستید. دستور پرداخت جدید ساخته می‌شود؛ مبلغ قبلی را واریز نکنید.")
                    markup={"inline_keyboard":[[{"text":"Cancel" if found['language']=='en' else "لغو","callback_data":"topup:cancel"}]]}
                else: reply="Choose an action below."; markup=back_menu(found['language'])
            api("answerCallbackQuery",{"callback_query_id":callback["id"]})
            edit_message(chat['id'],callback['message']['message_id'],reply,markup,html=True); return
        # Customer controls are inline and edit the same navigation message.
        # They never disclose panel, server, or inbound identities.
        if action.startswith(("menu:","buy:","guide:","service:")):
            invoice=None
            checkout=None
            trial_checkout=None
            with transaction() as conn:
                found=conn.execute("SELECT id,language,accepted_terms_version FROM users WHERE telegram_id=%s",(user_id,)).fetchone()
                if not found or not found["language"] or found["accepted_terms_version"]!=TERMS_VERSION:
                    api("answerCallbackQuery",{"callback_query_id":callback["id"]})
                    onboarding(chat["id"],found["language"] if found else None)
                    return
                language=found["language"]
                labels=LABELS[language]
                if action=="guide:platform":
                    reply='سیستم‌عامل خود را در آموزش اتصال انتخاب کنید؛ متن و ویدئوی راهنما آنجا در دسترس است.' if language=='fa' else 'Choose your device in the connection guide to view instructions and videos.'
                    markup={'inline_keyboard':[[{'text':'آموزش اتصال' if language=='fa' else 'Connection guide','web_app':{'url':f"https://{settings()['DOMAIN']}/app/?page=guide"},'style':'primary'}]]}
                elif action=="menu:home":
                    # A new menu avoids carrying stale detail buttons into the
                    # main view and keeps the conversation compact.
                    api("answerCallbackQuery",{"callback_query_id":callback["id"]})
                    try: delete_message(chat["id"],callback["message"]["message_id"])
                    except Exception: pass
                    message(chat["id"],main_menu_text(language),main_menu(language))
                    return
                elif action=="menu:buy":
                    categories={row['category'] for row in conn.execute("SELECT DISTINCT s.category FROM services s WHERE s.enabled AND NOT s.internal AND NOT s.trial_enabled AND s.category IN ('unlimited','volume_unlimited','volume_timed') AND EXISTS (SELECT 1 FROM plans p WHERE p.service_id=s.id AND p.enabled AND p.price_minor>0)").fetchall()}
                    categories.update(row['category'] for row in conn.execute("SELECT category FROM custom_volume_offers WHERE enabled").fetchall())
                    trial_service=conn.execute("SELECT s.id FROM services s WHERE s.enabled AND s.trial_enabled AND NOT EXISTS (SELECT 1 FROM trial_claims t WHERE t.user_id=%s) AND EXISTS (SELECT 1 FROM plans p WHERE p.service_id=s.id AND p.enabled AND p.price_minor=0 AND p.duration_hours BETWEEN 1 AND 72 AND p.traffic_gb>0 AND p.user_limit BETWEEN 1 AND 3) ORDER BY s.id LIMIT 1",(found['id'],)).fetchone()
                    rows=[]
                    if trial_service: rows.append([{"text":"✨ دریافت اکانت تست رایگان" if language=='fa' else "✨ Get your free test account","callback_data":f"buy:trial:{trial_service['id']}"}])
                    if 'unlimited' in categories: rows.append([{"text":"♾️ کانفیگ نامحدود" if language=='fa' else "♾️ Unlimited configurations","callback_data":"buy:category:unlimited"}])
                    if {'volume_unlimited','volume_timed'} & categories: rows.append([{"text":"◈ کانفیگ حجمی" if language=='fa' else "◈ Volume configurations","callback_data":"buy:category:volume"}])
                    markup={"inline_keyboard":rows+back_menu(language)["inline_keyboard"]}
                    reply=("🛒 <b>Buy a service</b>\n──────────\nFirst choose the type of configuration that fits your use. You choose a plan next, then see the payment method and final amount before any payment is made." if rows and language=="en" else "🛒 <b>خرید سرویس</b>\n──────────\nابتدا نوع کانفیگ مناسب استفاده‌تان را انتخاب کنید. در مرحلهٔ بعد پلن را می‌بینید و پیش از هر پرداخت، روش پرداخت و مبلغ نهایی به‌صورت شفاف نمایش داده می‌شود." if rows else user_error(language,'No plans are available' if language=='en' else 'فعلاً پلن فعالی نداریم','No paid plan is published right now.' if language=='en' else 'هیچ پلن پولی قابل خریدی در این لحظه منتشر نشده است.','Try again in a few minutes or contact Support.' if language=='en' else 'چند دقیقه بعد دوباره تلاش کنید یا از پشتیبانی بخواهید سرویس مناسب را فعال کند.'))
                elif action=="buy:category:unlimited":
                    # Unlimited products do not need an intermediate product
                    # selection: show their purchasable plans directly. This
                    # prevents customers from choosing the same-named product
                    # twice before they can reach checkout.
                    rows=conn.execute("""SELECT p.id,p.name,p.duration_hours,p.user_limit,p.price_minor
                      FROM plans p JOIN services s ON s.id=p.service_id
                      WHERE s.enabled AND NOT s.internal AND NOT s.trial_enabled
                        AND s.category='unlimited' AND p.enabled AND NOT p.is_custom AND p.price_minor>0
                      ORDER BY p.id LIMIT 20""").fetchall()
                    markup={"inline_keyboard":[[{"text":str(row['name'])[:60],"callback_data":f"buy:plan:{row['id']}"}] for row in rows]+[[{"text":"‹ Back" if language=='en' else "بازگشت ›","callback_data":"menu:buy"}]]}
                    reply=("∞ <b>Choose your unlimited plan</b>\n──────────\nChoose one option below to continue directly to checkout. The label is exactly the plan name set by the operator." if rows and language=='en' else "∞ <b>پلن نامحدود خود را انتخاب کنید</b>\n──────────\nبرای رفتن مستقیم به مرحلهٔ پرداخت، یکی از گزینه‌های زیر را انتخاب کنید. نام هر گزینه دقیقاً همان نامی است که مدیر تنظیم کرده است." if rows else user_error(language,'No unlimited plans are available' if language=='en' else 'پلن نامحدود فعالی نداریم','No purchasable unlimited plan is published at the moment.' if language=='en' else 'در حال حاضر هیچ پلن نامحدود قابل خریدی منتشر نشده است.','Return to Buy service and choose another type, or contact Support.' if language=='en' else 'به خرید سرویس برگردید و نوع دیگری را انتخاب کنید یا با پشتیبانی تماس بگیرید.'))
                elif action=="buy:category:volume":
                    kinds={row['category'] for row in conn.execute("SELECT DISTINCT s.category FROM services s WHERE s.enabled AND NOT s.internal AND NOT s.trial_enabled AND s.category IN ('volume_unlimited','volume_timed') AND EXISTS (SELECT 1 FROM plans p WHERE p.service_id=s.id AND p.enabled AND p.price_minor>0)").fetchall()}
                    kinds.update(row['category'] for row in conn.execute("SELECT category FROM custom_volume_offers WHERE enabled").fetchall())
                    rows=[]
                    if 'volume_unlimited' in kinds: rows.append([{"text":"◌ حجمی بدون انقضا" if language=='fa' else "◌ Volume · no expiry","callback_data":"buy:kind:volume_unlimited"}])
                    if 'volume_timed' in kinds: rows.append([{"text":"◷ حجمی با محدودیت زمانی" if language=='fa' else "◷ Volume · time limited","callback_data":"buy:kind:volume_timed"}])
                    markup={"inline_keyboard":rows+[[{"text":"‹ Back" if language=='en' else "بازگشت ›","callback_data":"menu:buy"}]]}
                    reply="◈ <b>Volume configurations</b>\n──────────\nChoose whether your service should end only when traffic is used, or have both a traffic limit and an expiry date." if language=='en' else "◈ <b>کانفیگ‌های حجمی</b>\n──────────\nانتخاب کنید سرویس فقط با تمام‌شدن حجم پایان پیدا کند یا هم محدودیت حجم داشته باشد و هم تاریخ انقضا."
                elif action.startswith('buy:kind:') and action.rsplit(':',1)[-1] in ('volume_unlimited','volume_timed'):
                    category=action.rsplit(':',1)[-1]
                    rows=conn.execute("SELECT s.id,s.name FROM services s WHERE s.enabled AND NOT s.internal AND NOT s.trial_enabled AND s.category=%s AND EXISTS (SELECT 1 FROM plans p WHERE p.service_id=s.id AND p.enabled AND p.price_minor>0) ORDER BY s.id LIMIT 20",(category,)).fetchall()
                    offer=conn.execute("SELECT service_id FROM custom_volume_offers WHERE category=%s AND enabled",(category,)).fetchone()
                    buttons=[[{"text":row['name'][:60],"callback_data":f"buy:service:{row['id']}"}] for row in rows]
                    if offer: buttons.append([{"text":("✨ Create your personal configuration" if category=='volume_unlimited' else "✨ Create your personal 30-day configuration") if language=='en' else ("✨ ساخت کانفیگ با حجم دلخواه" if category=='volume_unlimited' else "✨ ساخت کانفیگ دلخواه ۳۰ روزه"),"callback_data":f"buy:custom:{category}"}])
                    markup={"inline_keyboard":buttons+[[{"text":"‹ Back" if language=='en' else "بازگشت ›","callback_data":"buy:category:volume"}]]}
                    reply=("Choose a ready configuration, or create one with exactly the traffic you need." if offer else "Choose a volume configuration.") if language=='en' else ("یک پلن آماده انتخاب کنید یا با حجم دقیق موردنیاز خودتان کانفیگ بسازید." if offer else "سرویس حجمی موردنظر را انتخاب کنید.")
                elif action.startswith('buy:custom:') and action.rsplit(':',1)[-1] in ('volume_unlimited','volume_timed'):
                    category=action.rsplit(':',1)[-1]
                    offer=conn.execute("SELECT service_id FROM custom_volume_offers WHERE category=%s AND enabled",(category,)).fetchone()
                    if not offer or not _auto_location(conn,offer['service_id']):
                        reply=user_error(language,'Custom configuration is unavailable' if language=='en' else 'کانفیگ دلخواه فعلاً در دسترس نیست','No active route is ready for this configuration type.' if language=='en' else 'برای این نوع سرویس مسیر فعال و آماده‌ای پیدا نشد.','Return to Buy service and select a ready plan, or try again shortly.' if language=='en' else 'به صفحهٔ خرید برگردید و یک پلن آماده انتخاب کنید، یا چند دقیقه بعد دوباره امتحان کنید.'); markup=back_menu(language)
                    else:
                        conn.execute("INSERT INTO bot_payment_states(user_id,stage,entity_id) VALUES (%s,'custom_volume_amount',%s) ON CONFLICT (user_id) DO UPDATE SET stage='custom_volume_amount',entity_id=EXCLUDED.entity_id,provider=%s,updated_at=now()",(found['id'],offer['service_id'],category))
                        reply=("✨ Build your personal configuration\n──────────\nSend only a whole positive number for the traffic amount in GB. This configuration has no expiry date; it remains active until its traffic is used.\n\nFor example: 25" if category=='volume_unlimited' else "✨ Build your personal 30-day configuration\n──────────\nSend only a whole positive number for the traffic you want to use during the next 30 days, in GB.\n\nFor example: 25") if language=='en' else ("✨ کانفیگ شخصی شما\n──────────\nفقط مقدار حجم موردنیاز را به گیگ و به‌صورت عدد صحیح مثبت بفرستید. این کانفیگ تاریخ انقضا ندارد و تا پایان حجم فعال می‌ماند.\n\nمثال: ۲۵" if category=='volume_unlimited' else "✨ کانفیگ شخصی ۳۰ روزه\n──────────\nفقط مقدار حجمی را که می‌خواهید در ۳۰ روز مصرف کنید، به گیگ و به‌صورت عدد صحیح مثبت بفرستید.\n\nمثال: ۲۵")
                        markup={"inline_keyboard":[[{"text":"Cancel" if language=='en' else "لغو","callback_data":"menu:buy"}]]}
                elif action.startswith('buy:trial:') and action.rsplit(':',1)[-1].isdigit():
                    service_id=int(action.rsplit(':',1)[-1])
                    location_id=_auto_location(conn,service_id)
                    if not location_id:
                        reply=user_error(language,'Test account is unavailable' if language=='en' else 'اکانت تست فعلاً آماده نیست','No suitable server is available for a test account right now.' if language=='en' else 'در حال حاضر سرور مناسبی برای ساخت اکانت تست پیدا نشد.','Try again in a few minutes or contact Support.' if language=='en' else 'چند دقیقه بعد دوباره امتحان کنید یا از پشتیبانی کمک بگیرید.')
                        markup=back_menu(language)
                    else:
                        trial_checkout=(found['id'],service_id,location_id)
                        reply="در حال ساخت اکانت تست شما…" if language=='fa' else "Creating your test account…"
                        markup=back_menu(language)
                elif action.startswith("buy:service:") and action.rsplit(":",1)[-1].isdigit():
                    service_id=int(action.rsplit(":",1)[-1])
                    star_config=conn.execute("SELECT config FROM payment_settings WHERE provider='stars'",()).fetchone()
                    config=star_config["config"] if star_config else {}
                    if isinstance(config,str):
                        try: config=json.loads(config)
                        except ValueError: config={}
                    market_pricing=bool((config or {}).get("market_pricing_enabled"))
                    rows=conn.execute("SELECT p.id,p.name FROM plans p JOIN services s ON s.id=p.service_id WHERE p.service_id=%s AND p.enabled AND s.enabled AND NOT s.internal AND NOT s.trial_enabled AND NOT p.is_custom AND p.price_minor>0 ORDER BY p.id LIMIT 20",(service_id,)).fetchall()
                    markup={"inline_keyboard":[[{"text":row['name'][:55],"callback_data":f"buy:plan:{row['id']}"}] for row in rows]+[[{"text":"‹ Back" if language=="en" else "بازگشت ›","callback_data":"menu:buy"}]]}
                    reply=("📋 <b>Choose your plan</b>\n──────────\nEach option shows the duration, traffic, and simultaneous-IP limit. After choosing, Telegram shows the exact invoice before you pay." if rows and language=="en" else "📋 <b>انتخاب پلن</b>\n──────────\nهر گزینه مدت، حجم و محدودیت اتصال هم‌زمان را دارد. بعد از انتخاب، پیش‌فاکتور دقیق پیش از پرداخت در تلگرام نمایش داده می‌شود." if rows else user_error(language,'پلن آماده‌ای پیدا نشد','این سرویس فعلاً پلن فعال و قابل خرید ندارد.','به منوی خرید برگردید و سرویس دیگری را انتخاب کنید یا با پشتیبانی تماس بگیرید.'))
                elif action.startswith("buy:paymethod:") and len(action.split(':'))==4 and action.split(':')[2].isdigit() and action.split(':')[3] in ('stars','manual'):
                    _,_,raw_order_id,method=action.split(':')
                    order_id=int(raw_order_id)
                    order_row=conn.execute("SELECT o.id,o.plan_id,o.coupon_id,o.selection,p.is_custom FROM orders o JOIN plans p ON p.id=o.plan_id WHERE o.id=%s AND o.user_id=%s AND o.status='awaiting_payment' FOR UPDATE",(order_id,found['id'])).fetchone()
                    enabled=conn.execute("SELECT 1 FROM payment_settings WHERE provider=%s AND enabled",(method,)).fetchone()
                    if not order_row or not enabled:
                        reply=user_error(language,'Payment is unavailable' if language=='en' else 'پرداخت در دسترس نیست','This order is no longer available for the selected payment method.' if language=='en' else 'این سفارش دیگر با روش پرداخت انتخاب‌شده در دسترس نیست.','Return to My services and start again if needed.' if language=='en' else 'به «سرویس‌های من» برگردید و در صورت نیاز دوباره شروع کنید.'); markup=back_menu(language)
                    else:
                        coupon=conn.execute("SELECT code FROM coupons WHERE id=%s",(order_row['coupon_id'],)).fetchone() if order_row['coupon_id'] else None
                        selected=order_row['selection'] if isinstance(order_row['selection'],dict) else json.loads(order_row['selection'])
                        from .pricing import quote as price_quote
                        priced=price_quote(conn,plan_id=order_row['plan_id'],payment_method=method,users=(selected.get('users') if order_row['is_custom'] else None),traffic_gb=(selected.get('traffic_gb') if order_row['is_custom'] else None),duration_days=(selected.get('duration_days') if order_row['is_custom'] else None),coupon_code=(coupon['code'] if coupon else None))
                        conn.execute("UPDATE orders SET payment_method=%s,price_minor=%s,currency=%s,selection=%s WHERE id=%s",(method,priced.final_minor,priced.currency,json.dumps(priced.selection),order_id))
                        conn.execute("UPDATE payments SET provider=%s,amount_minor=%s,currency=%s,status='awaiting_payment',invoice_payload=NULL,provider_reference=NULL,updated_at=now() WHERE order_id=%s",(method,priced.final_minor,priced.currency,order_id))
                        if method=='manual':
                            destination=_manual_card_details(conn)
                            if not destination: raise ValueError('Manual destination is unavailable')
                            payment=conn.execute("SELECT id FROM payments WHERE order_id=%s AND provider='manual' FOR UPDATE",(order_id,)).fetchone()
                            conn.execute("INSERT INTO manual_payment_submissions(payment_id,customer_reference,review_status,receipt_path) VALUES (%s,'bot-chat','manual_review',NULL) ON CONFLICT (payment_id) DO UPDATE SET customer_reference='bot-chat',review_status='manual_review',receipt_path=NULL",(payment['id'],))
                            conn.execute("UPDATE payments SET status='manual_review' WHERE id=%s",(payment['id'],))
                            conn.execute("UPDATE orders SET status='manual_review' WHERE id=%s",(order_id,))
                            conn.execute("INSERT INTO bot_payment_states(user_id,stage,entity_id,provider) VALUES (%s,'topup_photo',%s,'order_manual') ON CONFLICT (user_id) DO UPDATE SET stage='topup_photo',entity_id=EXCLUDED.entity_id,provider='order_manual',updated_at=now()",(found['id'],order_id))
                            reply,markup=manual_order_instruction(order_id,priced.final_minor,destination,language)
                        else:
                            payment=conn.execute("SELECT o.id,o.price_minor,o.currency,o.payment_method,p.name,p.price_minor AS catalog_price,COALESCE(c.discount_percent,0) AS discount_percent FROM orders o JOIN plans p ON p.id=o.plan_id LEFT JOIN coupons c ON c.id=o.coupon_id WHERE o.id=%s",(order_id,)).fetchone()
                            invoice=(payment['id'],payment['name'],payment['price_minor']); reply=chat_payment_details(payment,language); markup=back_menu(language)
                elif action.startswith("buy:pay:") and action.rsplit(":",1)[-1].isdigit():
                    order_id=int(action.rsplit(':',1)[-1])
                    payment=conn.execute("""SELECT o.id,o.price_minor,o.currency,o.payment_method,p.name,p.price_minor AS catalog_price,
                      COALESCE(c.discount_percent,0) AS discount_percent
                      FROM orders o JOIN plans p ON p.id=o.plan_id LEFT JOIN coupons c ON c.id=o.coupon_id
                      WHERE o.id=%s AND o.user_id=%s AND o.status='awaiting_payment'""",(order_id,found['id'])).fetchone()
                    methods=[row['provider'] for row in conn.execute("SELECT provider FROM payment_settings WHERE enabled AND provider IN ('stars','manual') ORDER BY provider").fetchall()]
                    if not payment or not methods:
                        reply=user_error(language,'Payment is unavailable' if language=='en' else 'پرداخت در دسترس نیست','This order is no longer waiting for a Telegram payment.' if language=='en' else 'این سفارش دیگر در انتظار پرداخت تلگرامی نیست.','Open My services to check its current status, then start a new order if needed.' if language=='en' else 'برای بررسی وضعیت به «سرویس‌های من» بروید و در صورت نیاز سفارش جدیدی بسازید.')
                        markup=back_menu(language)
                    else:
                        reply=("💳 <b>Choose a payment method</b>\n──────────\nYour order is ready. Choose the method you want to use; the next page shows its exact instructions before any payment is made." if language=='en' else "💳 <b>روش پرداخت را انتخاب کنید</b>\n──────────\nسفارش شما آماده است. روش دلخواه را انتخاب کنید؛ در صفحهٔ بعد، جزئیات دقیق همان روش پیش از هر پرداخت نمایش داده می‌شود.")
                        labels={'stars':('⭐ Telegram Stars' if language=='en' else '⭐ استارز تلگرام'),'manual':('💳 Card transfer' if language=='en' else '💳 کارت‌به‌کارت')}
                        markup={"inline_keyboard":[[{"text":labels[method],"callback_data":f"buy:paymethod:{order_id}:{method}","style":"success"}] for method in methods]+back_menu(language)['inline_keyboard']}
                elif action.startswith("buy:plan:") and action.rsplit(":",1)[-1].isdigit():
                    plan_id=int(action.rsplit(":",1)[-1])
                    plan=conn.execute("SELECT p.id,p.name,p.service_id FROM plans p JOIN services s ON s.id=p.service_id WHERE p.id=%s AND p.enabled AND s.enabled AND NOT s.internal AND NOT p.is_custom",(plan_id,)).fetchone()
                    location_id=_auto_location(conn,plan["service_id"]) if plan else None
                    if not plan or not location_id:
                        reply=user_error(language,'این پلن فعلاً قابل خرید نیست','ممکن است پلن غیرفعال شده باشد یا ظرفیت لازم فراهم نباشد.','به فهرست پلن‌ها برگردید و گزینهٔ دیگری را انتخاب کنید؛ سپس دوباره امتحان کنید.')
                        markup=back_menu(language)
                    else:
                        # purchase() owns its own transaction and locks the
                        # selected capacity. Never call it while holding this
                        # navigation transaction, otherwise a small DB pool can
                        # deadlock under concurrent bot updates.
                        checkout=(found["id"],plan_id,location_id,plan["name"])
                        reply=("🧾 <b>Preparing your order summary</b>\n──────────\nWe are checking the selected service and its current price. Your payment method is shown in the next step." if language=="en" else "🧾 <b>در حال آماده‌سازی پیش‌فاکتور</b>\n──────────\nسرویس انتخاب‌شده و قیمت فعلی آن در حال بررسی است. روش پرداخت در مرحلهٔ بعد نمایش داده می‌شود.")
                        markup=back_menu(language)
                elif action=="menu:wallet":
                    balance=conn.execute("SELECT balance_minor FROM wallets WHERE user_id=%s",(found["id"],)).fetchone()["balance_minor"]
                    reply=(f"💳 <b>Your Alart wallet</b>\n──────────\nAvailable balance: <b>{toman(balance,language)}</b>\n\nUse Top up wallet to add credit. Your balance changes only after a payment is confirmed, so a pending payment never becomes usable too early." if language=="en" else f"💳 <b>کیف پول آلارت</b>\n──────────\nموجودی قابل استفاده: <b>{toman(balance,language)}</b>\n\nبرای افزایش موجودی، «شارژ کیف پول» را بزنید. موجودی فقط پس از تأیید پرداخت تغییر می‌کند؛ بنابراین مبلغ در حال بررسی قابل استفاده نیست.")
                    markup=back_menu(language,wallet=True)
                elif action=="menu:topup":
                    providers=conn.execute("SELECT provider FROM payment_settings WHERE enabled AND provider IN ('manual','stars','nowpayments') ORDER BY provider").fetchall()
                    labels_by_provider={'manual':('Card transfer' if language=='en' else 'کارت به کارت'),'stars':('Telegram Stars' if language=='en' else 'استارز تلگرام'),'nowpayments':('Crypto / NOWPayments' if language=='en' else 'رمزارز / NOWPayments')}
                    if providers:
                        reply=("➕ <b>Top up your wallet</b>\n──────────\nChoose a payment method below. For card transfer, pay first and then send a clear receipt for review. For Telegram Stars, Telegram confirms the payment directly. The exact amount is always shown before you continue." if language=='en' else "➕ <b>شارژ کیف پول</b>\n──────────\nروش پرداخت را انتخاب کنید. در کارت‌به‌کارت ابتدا واریز می‌کنید و سپس تصویر واضح رسید را می‌فرستید تا بررسی شود. در استارز، تلگرام پرداخت را مستقیم تأیید می‌کند. مبلغ نهایی همیشه پیش از ادامه نمایش داده می‌شود.")
                        markup={"inline_keyboard":[[{"text":labels_by_provider[x['provider']],"callback_data":f"topup:method:{x['provider']}"}] for x in providers]+back_menu(language)["inline_keyboard"][-1:]}
                    else:
                        reply=user_error(language,'No payment method is available' if language=='en' else 'فعلاً روش پرداخت فعالی نداریم','There is no verified payment method enabled at the moment.' if language=='en' else 'در حال حاضر هیچ روش پرداخت تأییدشده‌ای فعال نیست.','Try again later or contact Support for the available method.' if language=='en' else 'کمی بعد دوباره امتحان کنید یا برای اطلاع از روش فعال با پشتیبانی تماس بگیرید.')
                        markup=back_menu(language,wallet=True)
                elif action=="menu:services":
                    rows=conn.execute("""SELECT s.id,s.status,s.expires_at,p.name,c.client_name FROM subscriptions s JOIN orders o ON o.id=s.order_id JOIN plans p ON p.id=o.plan_id LEFT JOIN provisioned_configurations c ON c.subscription_id=s.id WHERE s.user_id=%s AND s.archived_at IS NULL ORDER BY s.id DESC LIMIT 12""",(found["id"],)).fetchall()
                    reply=("📦 <b>My services</b>\n──────────\nYour active and recent configurations appear below. Open any service to see remaining time and traffic, copy the subscription link, renew it, or open the connection guide." if language=='en' else "📦 <b>سرویس‌های من</b>\n──────────\nسرویس‌های فعال و اخیر شما پایین نمایش داده می‌شوند. هر سرویس را باز کنید تا زمان و حجم باقی‌مانده، لینک اشتراک، تمدید و راهنمای اتصال را ببینید.")
                    buttons=[[{"text":f"{'🟢' if row['status']=='active' else '🔴' if row['status']=='expired' else '🟡'} {row['name'][:36]} · {row['client_name'] or '#'+str(row['id'])}","callback_data":f"service:detail:{row['id']}","style":'success' if row['status']=='active' else 'danger' if row['status']=='expired' else 'primary'}] for row in rows]
                    markup={"inline_keyboard":buttons+back_menu(language)["inline_keyboard"]} if rows else back_menu(language)
                elif action.startswith('service:detail:') and action.rsplit(':',1)[-1].isdigit():
                    import html
                    sub=conn.execute("""SELECT s.id,s.status,s.expires_at,s.traffic_limit_bytes,s.traffic_used_bytes,s.provider_subscription_url,s.user_limit,s.remote_missing_at,p.name,p.price_minor,c.client_name FROM subscriptions s JOIN orders o ON o.id=s.order_id JOIN plans p ON p.id=o.plan_id LEFT JOIN provisioned_configurations c ON c.subscription_id=s.id WHERE s.id=%s AND s.user_id=%s""",(int(action.rsplit(':',1)[-1]),found['id'])).fetchone()
                    if not sub: reply=("Service not found." if language=='en' else "سرویس پیدا نشد."); markup=back_menu(language)
                    else:
                        from .service_delivery import service_message
                        reply,markup=service_message(sub,language)
                elif action.startswith('service:archive:') and action.rsplit(':',1)[-1].isdigit():
                    from .service_state import effective_status
                    sid=int(action.rsplit(':',1)[-1])
                    sub=conn.execute("SELECT * FROM subscriptions WHERE id=%s AND user_id=%s FOR UPDATE",(sid,found['id'])).fetchone()
                    if not sub or effective_status(sub) not in ('expired','cancelled','provisioning_failed'):
                        reply=user_error(language,'Service cannot be removed' if language=='en' else 'این سرویس قابل حذف نیست','Only an ended service can be removed from your list.' if language=='en' else 'فقط سرویس پایان‌یافته از فهرست شما حذف می‌شود.','Open My Services and choose an ended service.' if language=='en' else 'به «سرویس‌های من» برگردید و فقط سرویس پایان‌یافته را انتخاب کنید.')
                        markup=back_menu(language)
                    else:
                        conn.execute("UPDATE subscriptions SET archived_at=now() WHERE id=%s",(sid,))
                        reply=('🗑 <b>Service removed from your list</b>\n──────────\nThis ended service is no longer shown in My Services. It has not affected any active service or wallet balance.' if language=='en' else '🗑 <b>سرویس از فهرست شما حذف شد</b>\n──────────\nاین سرویس پایان‌یافته دیگر در «سرویس‌های من» نمایش داده نمی‌شود. هیچ تغییری در سرویس‌های فعال یا موجودی کیف پول شما ایجاد نشده است.')
                        markup=back_menu(language)
                elif action=="menu:account":
                    profile=conn.execute("""SELECT u.id,u.telegram_id,u.name,u.username,COALESCE(w.balance_minor,0) AS balance_minor,
                      COUNT(DISTINCT o.id) FILTER (WHERE o.status IN ('paid','provisioning','active')) AS purchase_count,
                      COUNT(DISTINCT s.id) FILTER (WHERE s.status IN ('active','expiring')) AS active_service_count,
                      EXISTS (SELECT 1 FROM trial_claims t WHERE t.user_id=u.id) AS used_trial
                      FROM users u
                      LEFT JOIN wallets w ON w.user_id=u.id
                      LEFT JOIN orders o ON o.user_id=u.id
                      LEFT JOIN subscriptions s ON s.user_id=u.id
                      WHERE u.id=%s
                      GROUP BY u.id,w.balance_minor""",(found['id'],)).fetchone()
                    reply=account_summary(profile,language); markup=back_menu(language)
                elif action=="menu:refer":
                    code=ensure_referral_code(conn,found["id"]); link=referral_link(code)
                    reply=(f"Your referral code: {code}\nReferral link: {link}" if language=="en" else f"کد معرفی شما: {code}\nلینک معرفی: {link}") if link else ("Referral link is unavailable right now." if language=="en" else "لینک معرفی اکنون در دسترس نیست.")
                    markup=back_menu(language)
                elif action=="menu:support":
                    reply=("💬 <b>Support</b>\n──────────\nFor help with an order, payment, or connection, tell us what you were doing and share the order or service number if you have one.\n\n🔒 Never send your subscription link, connection link, card data, or payment secret in a public group." if language=="en" else "💬 <b>پشتیبانی</b>\n──────────\nبرای کمک درباره سفارش، پرداخت یا اتصال، بنویسید در چه مرحله‌ای هستید و اگر دارید شمارهٔ سفارش یا سرویس را هم بفرستید.\n\n🔒 لینک اشتراک، لینک اتصال، اطلاعات کارت یا اطلاعات پرداخت را هرگز در گروه عمومی ارسال نکنید."); markup=back_menu(language)
                else:
                    reply="Choose an action below." if language=="en" else "یکی از گزینه‌های زیر را انتخاب کنید."; markup=main_menu(language)
            if checkout:
                try:
                    from .core import purchase
                    order=purchase(checkout[0],checkout[1],checkout[2],"bot:"+secrets.token_urlsafe(18),payment_method="stars")
                    if order["price_minor"]<=0 or order["currency"]!="XTR":
                        raise ValueError("Stars checkout unavailable")
                    with transaction() as conn:
                        conn.execute("UPDATE payments SET invoice_payload=%s,updated_at=now() WHERE order_id=%s AND provider='stars' AND status='pending'",(f"alart:order:{order['id']}",order["id"]))
                        wallet=conn.execute('SELECT balance_minor FROM wallets WHERE user_id=%s',(checkout[0],)).fetchone()
                        product=conn.execute("""SELECT s.description,s.trial_enabled,p.price_minor,
                          COALESCE(c.discount_percent,0) AS discount_percent
                          FROM services s JOIN plans p ON p.service_id=s.id
                          LEFT JOIN coupons c ON c.id=%s WHERE p.id=%s""",(order.get('coupon_id'),checkout[1])).fetchone()
                    balance=toman(wallet['balance_minor'],language) if wallet else '0'
                    reply=chat_order_summary(order,product,user_id,checkout[3],balance,language)
                    markup={"inline_keyboard":[[{"text":"ادامه به پرداخت" if language=='fa' else "Continue to payment","callback_data":f"buy:pay:{order['id']}","style":"success"}],*back_menu(language)["inline_keyboard"]]}
                except Exception as exc:
                    log.warning("Stars checkout could not be created: %s",type(exc).__name__)
                    reply=("Stars checkout is temporarily unavailable. Please try again shortly." if language=="en" else "پرداخت استارز موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید.")
                    markup=back_menu(language)
            if trial_checkout:
                try:
                    from .core import trial
                    trial(*trial_checkout)
                    reply="✨ اکانت تست رایگان شما ثبت شد و پس از آماده‌سازی در «سرویس‌های من» نمایش داده می‌شود." if language=='fa' else "✨ Your free test account has been requested. It will appear in My Services once provisioning finishes."
                except Exception as exc:
                    log.warning('Trial could not be created: %s',type(exc).__name__)
                    reply="اکانت تست قابل ایجاد نیست یا قبلاً استفاده شده است." if language=='fa' else "The test account is unavailable or has already been used."
                markup=back_menu(language)
            api("answerCallbackQuery",{"callback_query_id":callback["id"]})
            if not invoice:
                edit_message(chat["id"],callback["message"]["message_id"],reply,markup,html=action.startswith(('service:','menu:','buy:','guide:')))
            if invoice:
                link,payload=TelegramStars().invoice_link(order_id=invoice[0],title=invoice[1],amount=invoice[2])
                edit_message(chat['id'],callback['message']['message_id'],reply,{'inline_keyboard':[[{'text':'پرداخت و دریافت سرویس' if language=='fa' else 'Pay and receive service','url':link,'style':'success'}],*back_menu(language)['inline_keyboard']]},html=True)
            return
        language=None
        accepted=False
        with transaction() as conn:
            if action in ("lang:fa","lang:en"):
                language=action[-2:]
                conn.execute("INSERT INTO users(telegram_id,name,language) VALUES (%s,%s,%s) ON CONFLICT (telegram_id) DO UPDATE SET language=EXCLUDED.language",(user_id,(person.get("first_name") or "")[:100],language))
                conn.execute("INSERT INTO wallets(user_id) SELECT id FROM users WHERE telegram_id=%s ON CONFLICT DO NOTHING",(user_id,))
            elif action==f"terms:{TERMS_VERSION}":
                row=conn.execute("UPDATE users SET accepted_terms_version=%s,accepted_terms_at=now() WHERE telegram_id=%s AND language IS NOT NULL RETURNING language",(TERMS_VERSION,user_id)).fetchone()
                if row: language=row["language"]; accepted=True
        api("answerCallbackQuery",{"callback_query_id":callback["id"]})
        if accepted:
            # Remove stale customer keyboards, then present the inline menu.
            message(chat["id"],"✦ خوش آمدید. آماده‌اید از آلارت استفاده کنید." if language=="fa" else "✦ Welcome. Your Alart account is ready.",keyboard(language,user_id==int(settings()["ADMIN_TELEGRAM_ID"])))
            message(chat["id"],main_menu_text(language),main_menu(language))
        elif language: onboarding(chat["id"],language)
        return
    raw_text=(item.get("text") or "").strip()
    text=raw_text.split("@")[0].split(" ")[0]
    user=item.get("from") or {}
    telegram_id=user.get("id")
    chat=item.get("chat") or {}
    if (text=="/topicid" and telegram_id and chat.get("type")=="supergroup" and chat.get("id") and
        telegram_id==int(settings()["ADMIN_TELEGRAM_ID"])):
        payload={"chat_id":chat["id"],"text":f"Chat ID: {chat['id']}\nTopic ID: {item.get('message_thread_id') or 'General'}"}
        if item.get("message_thread_id"): payload["message_thread_id"]=item["message_thread_id"]
        api("sendMessage",payload)
        return
    if text=="/setup_topics" and telegram_id==int(settings()["ADMIN_TELEGRAM_ID"]) and chat.get("type")=="private":
        parts=raw_text.split()
        try:
            target=int(parts[1]) if len(parts)==2 else 0
            if target >= -1000000000000:
                raise ValueError("invalid chat id")
            created=setup_topics(target)
            message(chat["id"],f"Operational topics configured: {len(created)}")
        except Exception:
            # Do not include Telegram or group details in an admin-chat error.
            message(chat["id"],"Could not configure topics. Add the bot as a group administrator, enable Topics, then use /setup_topics <negative group ID>.")
        return
    if not telegram_id or not chat.get("id") or chat.get("type")!="private" or chat.get("id")!=telegram_id:
        return
    # Group-topic setup accepts a numeric Supergroup ID. Forwarded messages
    # are not reliable here because Telegram can hide their source chat.
    if telegram_id==int(settings()["ADMIN_TELEGRAM_ID"]):
        if raw_text in (LABELS["fa"]["topics"],LABELS["en"]["topics"]):
            with transaction() as conn:
                owner=conn.execute("SELECT id,language FROM users WHERE telegram_id=%s",(telegram_id,)).fetchone()
                if owner:
                    conn.execute("INSERT INTO bot_admin_states(user_id,stage) VALUES (%s,'topics_wait_group') ON CONFLICT (user_id) DO UPDATE SET stage=EXCLUDED.stage,updated_at=now()",(owner['id'],))
                    language=owner['language'] or 'en'
                else: language='en'
            message(chat['id'],("🧵 Set up operational report topics\n──────────\n1. Add Alart to your private Supergroup.\n2. Promote it to administrator and enable Topics in the group.\n3. Find the numeric group ID with a trusted ID bot and send it here. It normally starts with -100.\n\nUse /cancel or Back to main menu at any time." if language=='en' else "🧵 راه‌اندازی تاپیک‌های گزارش\n──────────\n۱. آلارت را به سوپرگروه خصوصی اضافه کنید.\n۲. بات را مدیر کنید و Topics را در گروه فعال کنید.\n۳. شناسهٔ عددی گروه را با یک بات معتبر دریافت کنید و همین‌جا بفرستید؛ معمولاً با ‎-100‎ شروع می‌شود.\n\nبرای لغو در هر زمان /cancel یا «بازگشت به منوی اصلی» را بزنید."),keyboard(language,True))
            return
        with transaction() as conn:
            owner=conn.execute("SELECT u.id,u.language FROM users u JOIN bot_admin_states s ON s.user_id=u.id WHERE u.telegram_id=%s AND s.stage='topics_wait_group' FOR UPDATE",(telegram_id,)).fetchone()
        if owner and (text in ('/start','/help','/cancel') or raw_text in (LABELS['fa']['home'],LABELS['en']['home'])):
            with transaction() as conn:
                conn.execute("DELETE FROM bot_admin_states WHERE user_id=%s",(owner['id'],))
            # /start and the reply-keyboard button continue into the normal
            # menu handler below; /cancel receives one concise confirmation.
            if text=='/cancel':
                message(chat['id'],"Report-group setup cancelled." if owner['language']=='en' else "راه‌اندازی گروه گزارش لغو شد.",keyboard(owner['language'],True))
                return
        elif owner:
            candidate=_digits(raw_text).replace(' ','')
            try: group_id=int(candidate)
            except ValueError: group_id=0
            if group_id >= -1000000000000:
                message(chat['id'],"Send the numeric Supergroup ID, usually beginning with -100. Use /cancel to leave this step." if owner['language']=='en' else "شناسهٔ عددی سوپرگروه را بفرستید که معمولاً با ‎-100‎ شروع می‌شود. برای خروج /cancel را بزنید.",keyboard(owner['language'],True)); return
            try:
                created=setup_topics(group_id)
            except Exception:
                log.warning("Topic setup failed for owner-selected group",exc_info=True)
                message(chat['id'],"The group could not be prepared. Check the numeric ID, make sure Alart is an administrator and Topics are enabled, then send the ID again. Use /cancel to leave." if owner['language']=='en' else "گروه آماده نشد. شناسهٔ عددی را بررسی کنید، مطمئن شوید آلارت مدیر گروه است و Topics فعال است؛ سپس شناسه را دوباره بفرستید. برای خروج /cancel را بزنید.",keyboard(owner['language'],True)); return
            with transaction() as conn:
                conn.execute("DELETE FROM bot_admin_states WHERE user_id=%s",(owner['id'],))
            message(chat['id'],(f"✅ {len(created)} operational report topics are ready." if owner['language']=='en' else f"✅ {len(created)} تاپیک گزارش عملیاتی آماده شد."),keyboard(owner['language'],True))
            return
    # Receipt handling is private, validates the actual downloaded image, then sends the original Telegram photo to operators.
    if item.get('photo'):
        with transaction() as conn:
            found=conn.execute("SELECT u.id,u.language,s.entity_id,s.provider FROM users u JOIN bot_payment_states s ON s.user_id=u.id WHERE u.telegram_id=%s AND s.stage='topup_photo' FOR UPDATE",(telegram_id,)).fetchone()
            if not found: return
            file_id=item['photo'][-1].get('file_id','')
            meta=api('getFile',{'file_id':file_id})
            remote_path=str(meta.get('file_path',''))
            if not file_id or not remote_path.startswith('photos/') or int(meta.get('file_size') or 0)>3*1024*1024: raise ValueError('Invalid receipt image')
            response=httpx.get(f"https://api.telegram.org/file/bot{settings()['BOT_TOKEN']}/{remote_path}",timeout=20)
            response.raise_for_status(); data=response.content
            from .payment_routes import _valid_receipt,_store_receipt
            _valid_receipt(data,'image/jpeg'); name,target=_store_receipt(data)
            try:
                if found['provider']=='order_manual':
                    payment=conn.execute("""SELECT p.id,o.id AS order_id FROM payments p JOIN orders o ON o.id=p.order_id
                      JOIN manual_payment_submissions m ON m.payment_id=p.id
                      WHERE o.id=%s AND o.user_id=%s AND o.status='manual_review' AND p.provider='manual' FOR UPDATE OF p,o,m""",(found['entity_id'],found['id'])).fetchone()
                    if not payment: raise ValueError('Order payment is unavailable')
                    conn.execute("UPDATE manual_payment_submissions SET receipt_path=%s,review_status='manual_review' WHERE payment_id=%s",(name,payment['id']))
                    conn.execute("DELETE FROM bot_payment_states WHERE user_id=%s",(found['id'],))
                    from .ops_topics import emit
                    emit(conn,'payment.manual_review',payment['order_id'])
                else:
                    row=conn.execute("SELECT id FROM wallet_topups WHERE id=%s AND user_id=%s AND status='awaiting_payment' FOR UPDATE",(found['entity_id'],found['id'])).fetchone()
                    if not row: raise ValueError('Top-up is unavailable')
                    conn.execute("UPDATE wallet_topups SET receipt_path=%s,status='manual_review',updated_at=now() WHERE id=%s",(name,row['id']))
                    conn.execute("DELETE FROM bot_payment_states WHERE user_id=%s",(found['id'],))
                    _admin_topup_alert(conn,row['id'],file_id)
            except Exception:
                target.unlink(missing_ok=True); raise
        message(chat['id'],('✅ <b>Receipt submitted for review</b>\n──────────\nWe received your receipt and will review it shortly. Your service is created only after approval.\n\nYou can open My services at any time to check the order status.' if found['language']=='en' else '✅ <b>رسید برای بررسی ارسال شد</b>\n──────────\nرسید شما دریافت شد و به‌زودی بررسی می‌شود. سرویس فقط پس از تأیید ساخته می‌شود.\n\nهر زمان می‌توانید از «سرویس‌های من» وضعیت سفارش را بررسی کنید.'),back_menu(found['language']),html=True); return
    raw_amount=_digits(raw_text)
    custom_checkout=None
    with transaction() as conn:
        state=conn.execute("SELECT u.id,u.language,s.stage,s.entity_id,s.provider FROM users u JOIN bot_payment_states s ON s.user_id=u.id WHERE u.telegram_id=%s FOR UPDATE",(telegram_id,)).fetchone()
        if state and state['stage']=='topup_amount':
            try: amount_toman=int(raw_amount)
            except ValueError: amount_toman=0
            details=_manual_card_details(conn)
            if not details:
                message(chat['id'],user_error(state['language'],'Card transfer is unavailable' if state['language']=='en' else 'کارت‌به‌کارت در دسترس نیست','The payment destination has not been configured yet.' if state['language']=='en' else 'مقصد پرداخت هنوز در پنل تنظیم نشده است.','Return to Wallet and choose another payment method, or contact support.' if state['language']=='en' else 'به کیف پول برگردید و روش دیگری را انتخاب کنید یا با پشتیبانی تماس بگیرید.'),back_menu(state['language'],wallet=True),html=True); return
            if amount_toman<1000 or amount_toman>1_000_000_000:
                message(chat['id'],user_error(state['language'],'Enter a valid amount' if state['language']=='en' else 'مبلغ معتبر وارد کنید','The amount must be a whole number between 1,000 and 1,000,000,000 Toman.' if state['language']=='en' else 'مبلغ باید یک عدد صحیح بین ۱٬۰۰۰ تا ۱٬۰۰۰٬۰۰۰٬۰۰۰ تومان باشد.','Send a new amount such as 100000. You can also cancel this top-up.' if state['language']=='en' else 'یک مبلغ جدید مانند 100000 بفرستید یا این شارژ را لغو کنید.'),{"inline_keyboard":[[{"text":"Cancel" if state['language']=='en' else "لغو","callback_data":"topup:cancel"}]]},html=True); return
            if state['provider']=='stars':
                from .stars_pricing import refresh_reference,settings as stars_settings,toman_to_stars
                # A financial invoice must use a fresh open-market rate; do not reuse an old cache.
                reference=refresh_reference()
                config_row=conn.execute("SELECT config FROM payment_settings WHERE provider='stars' FOR UPDATE").fetchone()
                current=json.loads(config_row['config']) if isinstance(config_row['config'],str) else (config_row['config'] or {})
                current.update(reference); current['rate_checked_at']=reference['rate_updated_at']
                conn.execute("UPDATE payment_settings SET config=%s,updated_at=now() WHERE provider='stars'",(json.dumps(current),))
                config=stars_settings(conn); stars=toman_to_stars(amount_toman*10,config)
                row=conn.execute("INSERT INTO wallet_topups(user_id,amount_minor,amount_minor_irr,provider,currency,request_key) VALUES (%s,%s,%s,'stars','XTR',%s) RETURNING id",(state['id'],stars,amount_toman*10,'bot:'+secrets.token_urlsafe(18))).fetchone()
                link,payload=TelegramStars().wallet_invoice_link(topup_id=row['id'],amount=stars)
                conn.execute("UPDATE wallet_topups SET invoice_payload=%s WHERE id=%s",(payload,row['id']))
                conn.execute("DELETE FROM bot_payment_states WHERE user_id=%s",(state['id'],))
                message(chat['id'],(f"Your wallet will receive {amount_toman:,} Toman.\n\nTelegram Stars required: {stars} ★\n\nTap the button below to pay securely with Telegram." if state['language']=='en' else f"موجودی کیف پول شما پس از پرداخت، {amount_toman:,} تومان افزایش پیدا می‌کند.\n\nمبلغ موردنیاز: {stars} استارز ★\n\nبرای پرداخت امن با تلگرام، دکمه زیر را بزنید."),{"inline_keyboard":[[{"text":"Pay with Telegram Stars" if state['language']=='en' else "پرداخت با استارز تلگرام","url":link}],[{"text":"Cancel" if state['language']=='en' else "لغو","callback_data":"menu:home"}]]}); return
            row=conn.execute("INSERT INTO wallet_topups(user_id,amount_minor,amount_minor_irr,provider,request_key) VALUES (%s,%s,%s,'manual',%s) RETURNING id",(state['id'],amount_toman*10,amount_toman*10,'bot:'+secrets.token_urlsafe(18))).fetchone()
            conn.execute("UPDATE bot_payment_states SET stage='topup_receipt',entity_id=%s,updated_at=now() WHERE user_id=%s",(row['id'],state['id']))
            text,buttons=manual_transfer_instruction(amount_toman,details,state['language'])
            message(chat['id'],text,buttons,html=True); return
        if state and state['stage']=='custom_volume_amount':
            try: gb=int(raw_amount)
            except ValueError: gb=0
            if gb<1 or gb>500 or str(gb)!=raw_amount:
                message(chat['id'],"Please send only a whole number from 1 to 500 (GB)." if state['language']=='en' else "فقط یک عدد صحیح از ۱ تا ۵۰۰ (گیگ) بفرستید."); return
            offer=conn.execute("SELECT o.category,o.service_id,o.fixed_duration_days FROM custom_volume_offers o WHERE o.service_id=%s AND o.enabled",(state['entity_id'],)).fetchone()
            location_id=_auto_location(conn,state['entity_id']) if offer else None
            if not offer or not location_id:
                conn.execute("DELETE FROM bot_payment_states WHERE user_id=%s",(state['id'],)); message(chat['id'],"This offer is temporarily unavailable." if state['language']=='en' else "این پیشنهاد موقتاً در دسترس نیست.",back_menu(state['language'])); return
            conn.execute("DELETE FROM bot_payment_states WHERE user_id=%s",(state['id'],))
            custom_checkout=(state['id'],state['entity_id'],location_id,gb,offer['fixed_duration_days'],state['language'],offer['category'])
        elif state and state['stage']=='admin_adjust':
            try: amount_toman=int(raw_amount)
            except ValueError: amount_toman=0
            if amount_toman<1000: message(chat['id'],'Enter a valid amount in Toman.'); return
            row=conn.execute("SELECT id,status FROM wallet_topups WHERE id=%s FOR UPDATE",(state['entity_id'],)).fetchone()
            if row and row['status'] in ('manual_review','more_info'):
                conn.execute("UPDATE wallet_topups SET amount_minor=%s,updated_at=now() WHERE id=%s",(amount_toman*10,row['id']))
                conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id,detail) VALUES (%s,'wallet_topup.adjust','wallet_topup',%s,%s)",(state['id'],row['id'],json.dumps({'amount_minor':amount_toman*10})))
                message(chat['id'],f"Top-up #{row['id']} adjusted to {amount_toman:,} Toman. Press Approve on the receipt message when verified.")
            conn.execute("DELETE FROM bot_payment_states WHERE user_id=%s",(state['id'],)); return
    if custom_checkout:
        try:
            from .core import purchase
            order=purchase(custom_checkout[0],custom_checkout[1],custom_checkout[2],"bot-custom:"+secrets.token_urlsafe(18),payment_method="stars",traffic_gb=custom_checkout[3],duration_days=(custom_checkout[4] or None))
            if order['price_minor']<=0 or order['currency']!='XTR': raise ValueError('Stars checkout unavailable')
            with transaction() as conn: conn.execute("UPDATE payments SET invoice_payload=%s,updated_at=now() WHERE order_id=%s AND provider='stars' AND status='pending'",(f"alart:order:{order['id']}",order['id']))
            link,_=TelegramStars().invoice_link(order_id=order['id'],title=('Custom volume configuration' if custom_checkout[5]=='en' else 'کانفیگ حجمی دلخواه'),amount=order['price_minor'])
            intro=(f"✨ Your {custom_checkout[3]} GB configuration is ready for checkout.\n──────────\nTelegram will confirm the exact Stars invoice below. Service provisioning starts only after confirmation." if custom_checkout[5]=='en' else f"✨ کانفیگ دلخواه {custom_checkout[3]} گیگ شما آماده پرداخت است.\n──────────\nمبلغ دقیق استارز در صورت‌حساب تلگرام نمایش داده می‌شود. ساخت سرویس فقط بعد از تأیید پرداخت شروع می‌شود.")
            message(chat['id'],intro,{"inline_keyboard":[[{"text":"Pay with Telegram Stars" if custom_checkout[5]=='en' else "پرداخت با استارز تلگرام","url":link}],[{"text":"Back" if custom_checkout[5]=='en' else "بازگشت","callback_data":"menu:buy"}]]}); return
        except Exception as exc:
            log.warning('Custom volume checkout failed: %s',type(exc).__name__)
            message(chat['id'],"Custom checkout is temporarily unavailable. Please try again." if custom_checkout[5]=='en' else "پرداخت کانفیگ دلخواه موقتاً در دسترس نیست. دوباره تلاش کنید.",back_menu(custom_checkout[5])); return
    referral_code=None
    with transaction() as conn:
        existed=conn.execute("SELECT 1 FROM users WHERE telegram_id=%s",(telegram_id,)).fetchone()
        found=conn.execute("INSERT INTO users(telegram_id,name) VALUES (%s,%s) ON CONFLICT (telegram_id) DO UPDATE SET name=EXCLUDED.name RETURNING id,language,accepted_terms_version",(telegram_id,(user.get("first_name") or "")[:100])).fetchone()
        conn.execute("INSERT INTO wallets(user_id) VALUES (%s) ON CONFLICT DO NOTHING",(found["id"],))
        if not existed:
            from .ops_topics import emit
            emit(conn,"users.new",found["id"])
        if text=="/start" and raw_text.startswith("/start r_"):
            claim_referral(conn,found["id"],raw_text.removeprefix("/start r_").strip())
        if telegram_id==int(settings()["ADMIN_TELEGRAM_ID"]):
            conn.execute("INSERT INTO admins(user_id,role) VALUES (%s,'owner') ON CONFLICT DO NOTHING",(found["id"],))
        admin_row=conn.execute("SELECT role FROM admins WHERE user_id=%s",(found["id"],)).fetchone()
        is_bot_admin=bool(admin_row and admin_row['role'] in ('owner','operator'))
        language=found["language"]
        labels=LABELS.get(language or "en",LABELS["en"])
        if not language or found["accepted_terms_version"]!=TERMS_VERSION:
            reply=None
        elif text=="/start" or text=="/help" or raw_text==labels.get("home"):
            reply="✦ به منوی اصلی آلارت برگشتید.\n\nاز دکمه‌های زیر برای مدیریت سریع حساب، کیف پول و سرویس‌ها استفاده کنید." if language=="fa" else "✦ You are back at Alart’s main menu.\n\nUse the controls below to manage your account, wallet, and services."
        elif text=="/wallet" or raw_text==labels["wallet"]:
            row=conn.execute("SELECT balance_minor FROM wallets WHERE user_id=%s",(found["id"],)).fetchone()
            reply=tr(language,"Wallet balance: {amount}",amount=toman(row['balance_minor'],language))
        elif text=="/services":
            rows=conn.execute("SELECT name FROM services WHERE enabled ORDER BY id LIMIT 20").fetchall()
            reply=tr(language,"Available services:\n{items}",items="\n".join("• "+r["name"] for r in rows) or tr(language,"No services available"))
        elif text=="/orders":
            rows=conn.execute("SELECT id,status FROM orders WHERE user_id=%s ORDER BY id DESC LIMIT 5",(found["id"],)).fetchall()
            reply=tr(language,"Recent orders:\n{items}",items="\n".join(f"#{r['id']} — {tr(language,r['status'])}" for r in rows) or tr(language,"No orders"))
        elif text=="/subscription" or raw_text==labels["services"]:
            rows=conn.execute("SELECT status,expires_at FROM subscriptions WHERE user_id=%s ORDER BY id DESC LIMIT 5",(found["id"],)).fetchall()
            reply=tr(language,"Subscriptions:\n{items}",items="\n".join(f"{tr(language,r['status'])} — {localized_date(language,r['expires_at'])}" for r in rows) or tr(language,"No subscriptions"))
        elif text=="/account" or raw_text==labels["account"]:
            reply=tr(language,"Account ID: {id}",id=found["id"])
        elif raw_text==labels["refer"]:
            referral_code=ensure_referral_code(conn,found["id"])
            reply=""
        elif (text=="/status" or raw_text==labels["status"]) and is_bot_admin and chat.get("type")=="private":
            failed=conn.execute("SELECT count(*) AS n FROM provisioning_jobs WHERE status='failed'").fetchone()["n"]
            alerts=conn.execute("SELECT count(*) AS n FROM alerts WHERE status<>'resolved'").fetchone()["n"]
            reply=tr(language,"System status\nFailed jobs: {failed}\nOpen alerts: {alerts}",failed=failed,alerts=alerts)
        elif text=="/alerts" and telegram_id==int(settings()["ADMIN_TELEGRAM_ID"]) and chat.get("type")=="private":
            rows=conn.execute("SELECT id,title FROM alerts WHERE status<>'resolved' ORDER BY id DESC LIMIT 10").fetchall()
            reply=tr(language,"Alerts:\n{items}",items="\n".join(f"#{r['id']} {r['title']}" for r in rows) or tr(language,"None"))
        elif (text=="/panel" or raw_text==labels["admin"]) and is_bot_admin and chat.get("type")=="private":
            code=secrets.token_urlsafe(24)
            conn.execute("INSERT INTO admin_login_codes(code_hash,user_id,expires_at) VALUES (%s,%s,now()+interval '5 minutes')",(hashlib.sha256(code.encode()).hexdigest(),found["id"]))
            reply=tr(language,"Panel login code (valid for 5 minutes):\n{code}\n\nOpen https://{domain}/panel/ and paste the code.",code=code,domain=settings()["DOMAIN"])
        elif text=="/web" and chat.get("type")=="private":
            code=secrets.token_urlsafe(24)
            conn.execute("INSERT INTO customer_login_codes(code_hash,user_id,expires_at) VALUES (%s,%s,now()+interval '5 minutes')",(hashlib.sha256(code.encode()).hexdigest(),found["id"]))
            reply=tr(language,"Web shop login code (valid for 5 minutes):\n{code}\n\nOpen https://{domain}/shop/ in your browser.",code=code,domain=settings()["DOMAIN"])
        elif text=="/support" or raw_text==labels["support"]:
            reply=tr(language,"For account and order help, use the support assistant in the Mini App or contact the operator of this bot.")
        else:
            reply="از دکمه‌های زیر استفاده کنید." if language=="fa" else "Choose an action below."
    if reply is None:
        onboarding(chat["id"],language)
    else:
        if referral_code:
            link=referral_link(referral_code)
            reply=(f"کد معرفی شما: {referral_code}\nلینک معرفی: {link}\nپاداش پس از اولین خرید موفق و فعال‌سازی سرویس ثبت می‌شود." if language=="fa" else
                   f"Your referral code: {referral_code}\nReferral link: {link}\nRewards are credited after the first successful purchase is provisioned.") if link else ("دریافت لینک معرفی اکنون ممکن نیست. بعداً دوباره تلاش کنید." if language=="fa" else "The referral link is unavailable right now. Please try again later.")
        if chat.get("type")=="private":
            # Keep only the two owner controls in a reply keyboard. All normal
            # customer actions remain attached to this message.
            message(chat["id"],reply,keyboard(language,is_bot_admin))
            if text in ("/start","/help") or raw_text==labels.get("home"): 
                message(chat["id"],main_menu_text(language),main_menu(language))
        else:
            message(chat["id"],reply)

def poll():
    offset=0
    while True:
        try:
            updates=api("getUpdates",{"offset":offset,"timeout":25,"allowed_updates":["message","pre_checkout_query","callback_query"]})
            for update in updates:
                offset=update["update_id"]+1
                try: handle(update)
                except Exception: log.exception("Bot update failed")
        except Exception:
            log.error("Bot polling failed")
            import time
            time.sleep(5)

def photo(chat_id, file_id, caption, reply_markup=None):
    payload={"chat_id":chat_id,"photo":file_id,"caption":caption[:1000]}
    if reply_markup: payload["reply_markup"]=reply_markup
    api("sendPhoto",payload)

def qr_photo(chat_id, png, caption):
    """Deliver an in-memory QR without storing a customer connection on Telegram servers ourselves.

    Telegram receives the image as the customer's private bot chat attachment.  The
    QR contents are never written to application logs or notification text.
    """
    with httpx.Client(timeout=35) as client:
        response=client.post(
            f"https://api.telegram.org/bot{settings()['BOT_TOKEN']}/sendPhoto",
            data={"chat_id":str(chat_id),"caption":caption[:1000]},
            files={"photo":("alart-connection.png",png,"image/png")},
        )
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("Telegram QR delivery failed")

def _digits(value):
    return str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).replace(",","").replace("٬","").strip()

def _manual_card_details(conn):
    row=conn.execute("SELECT public_details FROM payment_settings WHERE provider='manual' AND enabled").fetchone()
    return row["public_details"] if row else None

def manual_transfer_instruction(amount_toman, details, language):
    """Return a safe, copyable card-transfer instruction for Telegram HTML mode."""
    destination=str(details).strip()
    safe_destination=html.escape(destination,quote=False)
    if language=='fa':
        text=(f"🏦 <b>اطلاعات واریز کارت‌به‌کارت</b>\n──────────\n"
              f"لطفاً دقیقاً مبلغ <b>{amount_toman:,} تومان</b> را به مقصد زیر واریز کنید.\n\n"
              f"🔢 <b>شماره کارت / حساب مقصد</b>\n<code>{safe_destination}</code>\n\n"
              "✅ پس از پرداخت، دکمهٔ «ارسال رسید» را بزنید و تصویر واضح رسید را بفرستید.\n"
              "⏳ موجودی کیف پول فقط پس از بررسی رسید افزایش پیدا می‌کند.\n\n"
              "اگر هنوز پرداخت نکرده‌اید و مبلغ را می‌خواهید عوض کنید، ابتدا «تغییر مبلغ» را بزنید.")
        copy_label="📋 کپی شماره کارت / حساب"; receipt_label="🧾 ارسال رسید"; change_label="✏️ تغییر مبلغ"; cancel_label="لغو"
    else:
        text=(f"🏦 <b>Card transfer details</b>\n──────────\n"
              f"Please transfer exactly <b>{amount_toman:,} Toman</b> to the destination below.\n\n"
              f"🔢 <b>Card / account destination</b>\n<code>{safe_destination}</code>\n\n"
              "✅ After paying, tap Send receipt and upload a clear image of the receipt.\n"
              "⏳ Your wallet balance is credited only after the receipt is reviewed.\n\n"
              "Need a different amount before paying? Use Change amount first.")
        copy_label="📋 Copy card / account"; receipt_label="🧾 Send receipt"; change_label="✏️ Change amount"; cancel_label="Cancel"
    buttons={"inline_keyboard":[
        [{"text":copy_label,"copy_text":{"text":destination}}],
        [{"text":receipt_label,"callback_data":"topup:receipt"}],
        [{"text":change_label,"callback_data":"topup:change"},{"text":cancel_label,"callback_data":"topup:cancel"}],
    ]}
    return text,buttons

def chat_order_summary(order, product, telegram_id, service_name, wallet_balance, language):
    """Present the service-only pre-invoice before a payment method is chosen."""
    selected=order['selection']
    hours=int(selected.get('duration_hours') or 0)
    is_trial=bool((product or {}).get('trial_enabled'))
    if hours:
        duration=(f'{hours} ساعت' if language=='fa' else f'{hours} hours') if is_trial else (f'{(hours+23)//24} روز' if language=='fa' else f'{(hours+23)//24} days')
    else:
        duration='بدون انقضا' if language=='fa' else 'No expiry'
    traffic=f"{selected['traffic_gb']} GB" if selected.get('traffic_gb') else ('نامحدود' if language=='fa' else 'Unlimited')
    ips=selected.get('users') or ('نامحدود' if language=='fa' else 'Unlimited')
    note=(product or {}).get('description') or ''
    catalog_price=int((product or {}).get('price_minor') or 0)
    discount=int((product or {}).get('discount_percent') or 0)
    toman_price=toman(catalog_price*(100-discount)//100,language)
    if language=='fa':
        return (f"🧾 پیش‌فاکتور #{order['id']}\n\nشناسه تلگرام: {telegram_id}\nسرویس: {service_name}\nمدت اعتبار: {duration}\nحجم: {traffic}\nIP هم‌زمان: {ips}\n{note}\n\n"
                f"موجودی کیف پول: {wallet_balance}\nقیمت سرویس: {toman_price}\n\n"
                "🟡 سفارش شما آماده است. برای مشاهده روش‌های پرداخت و ادامه، دکمهٔ «ادامه به پرداخت» را بزنید. پس از تأیید پرداخت، سرویس ساخته و لینک و QR ارسال می‌شود.")
    return (f"🧾 Order summary #{order['id']}\n\nTelegram ID: {telegram_id}\nService: {service_name}\nDuration: {duration}\nTraffic: {traffic}\nConcurrent IPs: {ips}\n{note}\n\n"
            f"Wallet balance: {wallet_balance}\nService price: {toman_price}\n\n"
            "🟡 Your order is ready. Tap Continue to payment to view the available payment method and continue. Your link and QR will be delivered after payment confirmation and provisioning.")

def chat_payment_details(payment, language):
    """Payment information is intentionally separate from the service pre-invoice."""
    toman_price=toman(int(payment['catalog_price'])*(100-int(payment['discount_percent'] or 0))//100,language)
    stars=payment['price_minor']
    if language=='fa':
        return ("💳 <b>پرداخت سفارش</b>\n──────────\n"
                f"سفارش شما برای «{payment['name']}» آماده پرداخت است.\n"
                f"قیمت سرویس: <b>{toman_price}</b>\n\n"
                "روش فعال فعلی: <b>استارز تلگرام</b>\n"
                f"مبلغ قابل پرداخت: <b>{stars} ⭐</b>\n\n"
                "با زدن دکمهٔ زیر، صورت‌حساب امن تلگرام باز می‌شود. تلگرام مبلغ را تأیید می‌کند و پس از پرداخت موفق، ساخت سرویس به‌صورت خودکار شروع می‌شود.")
    return ("💳 <b>Order payment</b>\n──────────\n"
            f"Your order for “{payment['name']}” is ready for payment.\n"
            f"Service price: <b>{toman_price}</b>\n\n"
            "Current available method: <b>Telegram Stars</b>\n"
            f"Amount payable: <b>{stars} ⭐</b>\n\n"
            "Tap the button below to open Telegram’s secure invoice. Telegram confirms the charge, then provisioning starts automatically after a successful payment.")

def manual_order_instruction(order_id, amount_minor, destination, language):
    safe=html.escape(str(destination).strip(),quote=False)
    amount=toman(amount_minor,language)
    if language=='fa':
        text=(f"💳 <b>پرداخت کارت‌به‌کارت</b>\n──────────\n"
              f"برای سفارش #{order_id} دقیقاً مبلغ <b>{amount}</b> را به شمارهٔ زیر واریز کنید.\n\n"
              f"🔢 <b>شماره کارت / حساب مقصد</b>\n<code>{safe}</code>\n\n"
              "پس از واریز، همین‌جا یک تصویر واضح از رسید بفرستید. مبلغ، مقصد و جزئیات تراکنش باید خوانا باشند. رسید به‌صورت دستی بررسی می‌شود و پس از تأیید، ساخت سرویس شروع خواهد شد.")
        copy='📋 کپی شماره کارت / حساب'; back='بازگشت به منوی اصلی'
    else:
        text=(f"💳 <b>Card transfer payment</b>\n──────────\n"
              f"For order #{order_id}, transfer exactly <b>{amount}</b> to the destination below.\n\n"
              f"🔢 <b>Card / account destination</b>\n<code>{safe}</code>\n\n"
              "After transferring, send a clear receipt image here. The amount, destination, and transaction details must be visible. We review the receipt manually, then provisioning starts after approval.")
        copy='📋 Copy card / account'; back='Back to main menu'
    return text,{"inline_keyboard":[[{"text":copy,"copy_text":{"text":str(destination).strip()}}],[{"text":back,"callback_data":"menu:home","style":"danger"}] ]}

def account_summary(profile, language):
    """Render the customer profile without leaking unescaped Telegram data into HTML."""
    name=html.escape(str(profile['name'] or ('کاربر آلارت' if language=='fa' else 'Alart customer')),quote=False)
    username=html.escape(str(profile['username'] or ''),quote=False)
    username_display=f"@{username}" if username else ('ثبت نشده' if language=='fa' else 'Not set')
    trial_used=bool(profile['used_trial'])
    if language=='fa':
        return (f"👤 <b>حساب کاربری شما</b>\n──────────\n"
                f"👋 نام: <b>{name}</b>\n🔗 نام کاربری: <b>{username_display}</b>\n"
                f"🆔 شناسهٔ تلگرام: <code>{profile['telegram_id']}</code>\n"
                f"📁 شناسهٔ داخلی: <code>{profile['id']}</code>\n\n"
                f"💳 موجودی کیف پول: <b>{toman(profile['balance_minor'],language)}</b>\n"
                f"🛍 خریدهای موفق: <b>{profile['purchase_count']}</b>\n"
                f"📡 سرویس‌های فعال: <b>{profile['active_service_count']}</b>\n"
                f"🧪 اکانت تست: <b>{'استفاده شده' if trial_used else 'هنوز استفاده نشده'}</b>\n\n"
                "از بخش‌های پایین می‌توانید سرویس‌ها، خریدها و کیف پول خود را مدیریت کنید.")
    return (f"👤 <b>Your account</b>\n──────────\n"
            f"👋 Name: <b>{name}</b>\n🔗 Username: <b>{username_display}</b>\n"
            f"🆔 Telegram ID: <code>{profile['telegram_id']}</code>\n"
            f"📁 Internal ID: <code>{profile['id']}</code>\n\n"
            f"💳 Wallet balance: <b>{toman(profile['balance_minor'],language)}</b>\n"
            f"🛍 Successful purchases: <b>{profile['purchase_count']}</b>\n"
            f"📡 Active services: <b>{profile['active_service_count']}</b>\n"
            f"🧪 Trial account: <b>{'Used' if trial_used else 'Not used yet'}</b>\n\n"
            "Use the menu below to manage your services, purchases, and wallet.")

def _admin_topup_alert(conn, topup_id, receipt_file_id):
    row=conn.execute("""SELECT w.id,w.amount_minor,w.customer_reference,u.telegram_id,u.name,u.username
      FROM wallet_topups w JOIN users u ON u.id=w.user_id WHERE w.id=%s""",(topup_id,)).fetchone()
    if not row: return
    text=(f"Card transfer receipt\nTop-up #{row['id']}\nAmount: {row['amount_minor']//10:,} Toman\n"
          f"User: {row['name'] or '—'} · @{row['username'] or '—'}\nTelegram ID: {row['telegram_id']}\n"
          f"Reference: {row['customer_reference'] or '—'}")
    markup={"inline_keyboard":[
      [{"text":"✅ Approve","callback_data":f"admin:topup:{topup_id}:approve"},{"text":"✏️ Adjust amount","callback_data":f"admin:topup:{topup_id}:adjust"}],
      [{"text":"❌ Reject","callback_data":f"admin:topup:{topup_id}:reject"},{"text":"ℹ️ More info","callback_data":f"admin:topup:{topup_id}:more_info"}]]}
    admins=conn.execute("SELECT u.telegram_id FROM admins a JOIN users u ON u.id=a.user_id WHERE a.role IN ('owner','operator')").fetchall()
    for admin in admins:
        try: photo(admin['telegram_id'],receipt_file_id,text,markup)
        except Exception: log.exception("Could not deliver manual receipt to administrator")
