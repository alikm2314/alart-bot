const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context={
  window:{},
  navigator:{language:'en-US'},
  localStorage:{getItem:()=>null,setItem:()=>{}},
  document:{readyState:'loading',body:null,documentElement:{},addEventListener:()=>{}},
};
vm.runInNewContext(fs.readFileSync('web/common/i18n.js','utf8'),context);
const i18n=context.window.AlartI18n;
assert.equal(i18n.t('Wallet'),'Wallet');
i18n.setLanguage('fa');
assert.equal(context.document.documentElement.dir,'rtl');
assert.equal(i18n.t('Wallet'),'کیف پول');
assert.equal(i18n.t('Control center'),'مرکز مدیریت');
assert.equal(i18n.t('Find a section or record…'),'جست‌وجوی بخش یا مورد…');
assert.match(i18n.t('Send /panel to your Alart bot in a private chat, then enter the one-time code.'),/کد یک‌بارمصرف/);
assert.equal(i18n.t('Your service has expired.'),'Your service has expired.'); // server notification layer
assert.equal(i18n.t('Welcome back, Ali.'),'خوش برگشتید، Ali.');
i18n.setLanguage('en');
assert.equal(context.document.documentElement.dir,'ltr');
assert.equal(i18n.t('Wallet'),'Wallet');
console.log('i18n smoke tests passed');
