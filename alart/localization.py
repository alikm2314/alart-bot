"""Server-side user messages; provider data and secrets never enter translations."""
from datetime import datetime, timezone
import re

FA = {
    'Your service is active. Your subscription link and connection guidance are ready.':'سرویس شما فعال شد. لینک اشتراک و آموزش اتصال آماده است.',
    "Payment received. Your service is being provisioned.": "پرداخت دریافت شد. سرویس شما در حال ساخت است.",
    "Your service is active. Open Alart to view its configuration.": "سرویس شما فعال شد. برای مشاهده اطلاعات اتصال، آلارت را باز کنید.",
    "We could not activate your service. Support has been alerted.": "فعال‌سازی سرویس ممکن نشد. به پشتیبانی اطلاع داده شد.",
    "Your service renewal is active.": "تمدید سرویس شما فعال شد.",
    "Your service has expired.": "اعتبار سرویس شما به پایان رسیده است.",
    "Your service traffic allowance has been used.": "حجم سرویس شما به پایان رسیده است.",
    "Your manual payment was rejected. Open Alart for details.": "پرداخت دستی شما رد شد. برای جزئیات، آلارت را باز کنید.",
    "More information is needed for your manual payment. Open the web shop to update your reference or receipt.": "برای پرداخت دستی شما اطلاعات بیشتری لازم است. در فروشگاه وب، شماره پیگیری یا رسید را به‌روزرسانی کنید.",
    "Wallet balance: {amount}": "موجودی کیف پول: {amount}",
    "Available services:\n{items}": "سرویس‌های موجود:\n{items}",
    "Recent orders:\n{items}": "سفارش‌های اخیر:\n{items}",
    "Subscriptions:\n{items}": "سرویس‌های شما:\n{items}",
    "Account ID: {id}": "شناسه حساب: {id}",
    "System status\nFailed jobs: {failed}\nOpen alerts: {alerts}": "وضعیت سیستم\nکارهای ناموفق: {failed}\nهشدارهای باز: {alerts}",
    "Alerts:\n{items}": "هشدارها:\n{items}",
    "None": "موردی نیست", "No services available": "سرویسی موجود نیست", "No orders": "سفارشی وجود ندارد",
    "No subscriptions": "سرویسی وجود ندارد", "pending": "در انتظار", "active": "فعال", "expired": "منقضی", "failed": "ناموفق",
    "provisioning_failed": "خطا در ساخت سرویس", "refunded": "بازپرداخت شده", "expiring": "رو به پایان",
    "For account and order help, use the support assistant in the Mini App or contact the operator of this bot.":
        "برای کمک درباره حساب یا سفارش، از دستیار مینی‌اپ استفاده کنید یا با پشتیبانی تماس بگیرید.",
    "Web shop login code (valid for 5 minutes):\n{code}\n\nOpen https://{domain}/shop/ in your browser.":
        "کد ورود فروشگاه وب (اعتبار ۵ دقیقه):\n{code}\n\nدر مرورگر خود نشانی https://{domain}/shop/ را باز کنید.",
    "Panel login code (valid for 5 minutes):\n{code}\n\nOpen https://{domain}/panel/ and paste the code.":
        "کد ورود پنل (اعتبار ۵ دقیقه):\n{code}\n\nنشانی https://{domain}/panel/ را باز کنید و کد را وارد کنید.",
    "🎁 Referral reward\nYour referred user completed their first purchase. {amount} has been added to your wallet.":
        "🎁 پاداش معرفی\nکاربر معرفی‌شده نخستین خرید خود را تکمیل کرد. {amount} به کیف پول شما افزوده شد.",
    "Your referral reward was reversed after the qualifying order was refunded.":
        "پس از بازپرداخت سفارش واجد شرایط، پاداش معرفی از کیف پول شما برگشت داده شد.",
    "A refunded referral reward is awaiting reversal. Wallet purchases are paused until the balance covers it.":
        "پاداش معرفی مربوط به سفارش بازپرداخت‌شده در انتظار برگشت است. خرید با کیف پول تا زمانی که موجودی کافی شود متوقف می‌ماند.",
    "Your refund is confirmed. Open Alart to view the status.": "بازپرداخت شما تأیید شد. وضعیت را در آلارت ببینید.",
    "Your cancellation request was approved.": "درخواست لغو شما تأیید شد.",
    "Your cancellation request was rejected. Contact support for details.": "درخواست لغو شما رد شد. برای جزئیات با پشتیبانی تماس بگیرید.",
    "Your service is cancelled. Refund status is shown separately in Alart.": "سرویس شما لغو شد. وضعیت بازپرداخت به‌صورت جداگانه در آلارت نمایش داده می‌شود.",
    "Payment received after cancellation. Your refund is being processed.": "پرداخت پس از لغو دریافت شد. بازپرداخت شما در حال پیگیری است.",
    "Your service is approaching its expiration date. Renew it in Alart.": "اعتبار سرویس شما به‌زودی پایان می‌یابد. آن را در آلارت تمدید کنید.",
    "We could not renew your service. Your existing service remains unchanged; refund processing has started.": "تمدید سرویس ممکن نشد. سرویس فعلی شما بدون تغییر باقی مانده و پیگیری بازپرداخت آغاز شده است.",
    "Your renewal payment failed or expired. Your existing service was not changed.": "پرداخت تمدید ناموفق یا منقضی شد. سرویس فعلی شما تغییری نکرده است.",
    "Your renewal checkout was cancelled. Your existing service was not changed.": "پرداخت تمدید لغو شد. سرویس فعلی شما تغییری نکرده است.",
    "This order is no longer available. Please start checkout again.": "این سفارش دیگر در دسترس نیست. لطفاً فرایند خرید را دوباره شروع کنید.",
    "requested": "درخواست‌شده", "approved": "تأییدشده", "refund_pending": "در انتظار بازپرداخت",
    "processing": "در حال پردازش", "completed": "تکمیل‌شده", "cancelled": "لغوشده",
}


def language(value: str | None) -> str:
    return "fa" if value == "fa" else "en"


def tr(lang: str | None, template: str, **values) -> str:
    if language(lang)=='fa':
        reminder=re.fullmatch(r'Your service expires on (\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC)\. Renew it in Alart\.',template)
        if reminder:
            return f'اعتبار سرویس شما در {reminder.group(1)} پایان می‌یابد. آن را در آلارت تمدید کنید.'
    translated = FA.get(template, template) if language(lang) == "fa" else template
    return translated.format(**values) if values else translated


def localized_date(lang: str | None, value: datetime | None) -> str:
    if not value:
        return tr(lang, "pending")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
