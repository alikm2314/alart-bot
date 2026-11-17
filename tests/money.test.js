const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context={window:{AlartI18n:{language:'en',number:(value)=>new Intl.NumberFormat('en-US').format(value)}},Intl,BigInt};
vm.runInNewContext(fs.readFileSync('web/common/money.js','utf8'),context);
assert.equal(context.window.AlartMoney.format(1250,'USD'),'12.50 USD');
assert.equal(context.window.AlartMoney.format(-105,'USD'),'−1.05 USD');
assert.equal(context.window.AlartMoney.format(150,'XTR'),'150 XTR');
console.log('money display tests passed');
