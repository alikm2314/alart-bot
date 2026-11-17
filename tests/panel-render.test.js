const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const source=fs.readFileSync('web/panel/panel.js','utf8');
const prefix=source.slice(0,source.indexOf('function showLogin'));
const context={
  window:{AlartI18n:{language:'en',t:value=>value}},
  document:{documentElement:{dataset:{}}},
  localStorage:{getItem:()=>null,setItem:()=>{}},
};
vm.createContext(context);
vm.runInContext(prefix,context);
const rendered=vm.runInContext("card('TR-Istanbul-01', 'body', badge('online'))",context);
assert.match(rendered,/<h2>TR-Istanbul-01<span class="badge">online<\/span><\/h2>/);
assert.doesNotMatch(rendered,/&lt;span class=/);
const hostile=vm.runInContext("card('<img src=x>', 'body', badge('online'))",context);
assert.match(hostile,/&lt;img src=x&gt;/);
assert.doesNotMatch(hostile,/<img src=x>/);
console.log('panel card renders the name and controlled badge without escaped markup');
