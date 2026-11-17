/* PostgreSQL stores IRR integers. Only presentation converts to toman. */
window.AlartMoney = Object.freeze({
  toman(irr) {
    const amount = BigInt(irr ?? 0);
    const sign = amount < 0n ? '−' : '';
    const positive = amount < 0n ? -amount : amount;
    const whole = positive / 10n;
    const fraction = positive % 10n;
    const language=window.AlartI18n?.language||'en';
    const locale=language==='fa'?'fa-IR':'en-US';
    return `${sign}${new Intl.NumberFormat(locale).format(whole)}${fraction ? `${language==='fa'?'٫':'.'}${new Intl.NumberFormat(locale,{useGrouping:false}).format(fraction)}` : ''} ${language==='fa'?'تومان':'Toman'}`;
  },
  format(amount, currency) {
    if(currency==='IRR')return this.toman(amount);
    if(currency==='USD'){
      const cents=BigInt(amount??0),negative=cents<0n,positive=negative?-cents:cents;
      const language=window.AlartI18n?.language||'en',locale=language==='fa'?'fa-IR':'en-US';
      const whole=new Intl.NumberFormat(locale).format(positive/100n);
      const fraction=new Intl.NumberFormat(locale,{minimumIntegerDigits:2,useGrouping:false}).format(positive%100n);
      return `${negative?'−':''}${whole}${language==='fa'?'٫':'.'}${fraction} USD`;
    }
    return `${window.AlartI18n?.number(amount)??new Intl.NumberFormat().format(amount??0)} ${currency}`;
  }
});
