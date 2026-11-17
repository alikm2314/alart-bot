let tg=window.Telegram?.WebApp;
document.querySelector('#telegram-sdk')?.addEventListener('load',()=>{tg=window.Telegram?.WebApp;tg?.ready();tg?.expand()});
tg?.ready(); tg?.expand();
const $=(selector)=>document.querySelector(selector);
const clean=(value)=>String(value??'').replace(/[&<>"']/g,(char)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const money=(value)=>window.AlartI18n.number(value);
const toman=(value)=>window.AlartMoney.toman(value);
const displayMoney=(value,currency)=>window.AlartMoney.format(value,currency);
const themeChoices=['system','dark','light'];
const tr=(value)=>window.AlartI18n.t(value);
const status=(value)=>{const raw=String(value||'');return tr(raw)===raw?tr(raw.replaceAll('_',' ')):tr(raw)};
function setTheme(value){const theme=themeChoices.includes(value)?value:'system';document.documentElement.dataset.theme=theme;localStorage.setItem('alart-theme',theme);if(theme!=='system'){try{tg?.setHeaderColor?.(theme==='dark'?'#08111f':'#f5f7fb')}catch{}}}
setTheme(localStorage.getItem('alart-theme')||'system');
const state={notifications:[],page:'home',catalog:null,me:null,account:null,category:null,service:null,plan:null,quote:null,limits:null,checkoutKey:null,referral:null,renewal:null,renewalChoice:null,renewalKey:null,topup:null,paymentMethod:'stars',supportContact:null};

function toast(message){const node=$('#toast');node.textContent=window.AlartI18n.t(message);node.classList.add('show');setTimeout(()=>node.classList.remove('show'),3800)}
async function api(path,options={}){const response=await fetch('/api/'+path,{credentials:'same-origin',headers:{'Content-Type':'application/json','Accept-Language':window.AlartI18n.language},...options});const data=await response.json();if(!response.ok)throw Error(tr(Array.isArray(data.detail)?'Check the information and try again.':data.detail||'Request failed'));return data}
async function post(path,body){return api(path,{method:'POST',body:JSON.stringify(body)})}
async function refresh(){[state.me,state.account]=await Promise.all([api('me'),api('account')])}
function button(label,id,kind='primary'){return `<button class="${kind} full" id="${id}">${label}</button>`}
function backButton(id,label){return `<button class="text-link back-link" id="${id}"><span class="back-arrow" aria-hidden="true">←</span> ${label}</button>`}
function heading(title,subtitle){return `<h1 class="page-title">${title}</h1><p class="subtitle">${subtitle}</p>`}
function empty(title,detail,label='Back to home',page='home'){return `<div class="empty"><b>${title}</b><p>${detail}</p>${label?`<button class="primary" data-go="${page}">${label}</button>`:''}</div>`}

async function reportBootFailure(stage,error){try{await fetch('/api/client-failures',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage,error:String(error?.message||error||'unknown').slice(0,160)})})}catch{}}
async function boot(){
  let bootStage='telegram';
  try{
    const initData=tg?.initData||new URLSearchParams(location.hash.slice(1)).get('tgWebAppData');
    if(!initData)throw Error('Open Alart inside Telegram');
    bootStage='authentication'; const authenticated=await post('auth/telegram',{init_data:initData,include_bootstrap:true});
    bootStage='bootstrap'; const initial=authenticated.bootstrap||await api('bootstrap');
    bootStage='normalize'; state.catalog={services:[],plans:[],locations:[],announcements:[],...(initial.catalog||{})};
    state.me=initial.me||{};
    state.account={orders:[],subscriptions:[],transactions:[],...(initial.account||{})};
    state.notifications=initial.notifications||[];state.supportContact=initial.support_contact||null;state.trialEligible=Boolean(initial.trial_eligible);
    // Localization is presentational. A browser-specific translation failure must never block the Mini App.
    try{if(state.me.language)window.AlartI18n?.setLanguage?.(state.me.language)}catch(error){console.warn('Language synchronization skipped',error)}
    bootStage='profile'; const telegramPhoto=tg?.initDataUnsafe?.user?.photo_url;
    if(/^https:\/\//.test(telegramPhoto||''))state.me.photo_url=telegramPhoto;
    const initialLetter=(state.me.name||'A').slice(0,1).toUpperCase();
    const avatar=$('#avatar');
    if(avatar){
      avatar.textContent=initialLetter;
      if(/^https:\/\//.test(state.me.photo_url||''))avatar.innerHTML='<img src="/api/me/avatar" alt="">';
    }
    bootStage='render'; render();
    bootStage='route'; const route=new URLSearchParams(location.search), requested=Number(route.get('renew'));
    const coupon=route.get('coupon'); if(/^[A-Za-z0-9_-]{1,40}$/.test(coupon||''))state.promoCoupon=coupon.toUpperCase();
    if(Number.isSafeInteger(requested)&&requested>0){history.replaceState(null,'',location.pathname+location.hash);await openRenewal(requested)}else if(['notifications','my-services','guide','services','account'].includes(route.get('page'))){navigate(route.get('page'))}else if(route.get('topup')==='1'){history.replaceState(null,'',location.pathname+location.hash);state.topup=null;navigate('topup')}
  }catch(error){
    console.error('Alart Mini App boot failed',bootStage,error);
    void reportBootFailure(bootStage,error);
    const content=$('#content');
    if(content)content.innerHTML=`<section class="boot-error">${empty('Unable to open Alart','Please close this Mini App and open it again from Telegram.')}<div class="retry-wrap">${button('Try again','retry','ghost')}</div></section>`;
    const retry=$('#retry'); if(retry)retry.onclick=()=>location.reload();
  }
}
function navigate(page){if(!state.account||!state.catalog)return;state.page=page;document.querySelectorAll('nav button').forEach((node)=>node.classList.toggle('active',node.dataset.page===page));render();scrollTo(0,0);if(page==='referrals'||page==='account')loadReferrals();if(page==='guide')loadGuides();if(page==='my-services')refresh().then(()=>{if(state.page===page)render()}).catch(error=>toast(error.message));if(page==='notifications')api('notifications').then(items=>{state.notifications=items;if(state.page===page)render()}).catch(error=>toast(error.message))}
document.querySelectorAll('nav button').forEach((node)=>node.onclick=()=>navigate(node.dataset.page));
const avatarButton=$('#avatar'); if(avatarButton)avatarButton.onclick=()=>navigate('account');
const supportButton=$('#support-head'); if(supportButton)supportButton.onclick=()=>navigate('support');
function render(){
  const views={home:home,services:services,'my-services':myServices,category:category,products:products,product:product,builder:builder,wallet:wallet,account:account,guide:guide,notifications:notificationCenter,referrals:referrals,support:support,order:order,renewal:renewal,topup:topup,'manual-order':manualOrder};
  $('#content').innerHTML=(views[state.page]||home)();
  const avatar=$('#avatar'), notifications=$('#notifications'), supportHeader=$('#support-head');
  if(avatar)avatar.hidden=state.page==='account';
  if(notifications)notifications.hidden=state.page==='notifications';
  if(supportHeader)supportHeader.hidden=state.page==='support';
  try{window.AlartI18n?.translate?.()}catch(error){console.warn('Translation render skipped',error)}
  bind();
}

function notificationCenter(){return `${heading('Notifications','Recent service, payment, and account updates.')}<div class="notification-back-wrap"><button class="text-link back-link notification-back" id="back-notifications">← Back</button></div>${state.notifications.map((item)=>`<div class="card"><p>${clean(item.body)}</p><small>${window.AlartI18n.date(item.created_at)}</small>${item.action_path?`<button class="text-link" data-notification-action="${clean(item.action_path)}">Open</button>`:''}</div>`).join('')||empty('No notifications','You are all caught up.')}`}

function home(){
  const active=state.account.subscriptions.filter(sub=>['active','expiring'].includes(serviceStatus(sub)));
  const trialService=state.catalog.services.find(service=>service.trial_enabled);
  return `<section class="wallet-hero"><span>Wallet balance</span><strong>${toman(state.me.balance_minor)}</strong><div class="actions"><button class="primary" data-go="topup">Top up wallet</button><button class="ghost" data-go="wallet">Transaction history</button></div></section>
    <div class="home-shortcuts"><button data-go="services"><svg><use href="#i-shop"/></svg><strong>Buy service</strong></button><button data-go="my-services"><svg><use href="#i-link"/></svg><strong>My services</strong></button><button data-go="account"><svg><use href="#i-user"/></svg><strong>My account</strong></button><button data-go="support"><svg><use href="#i-support"/></svg><strong>Support</strong></button></div>
    ${state.trialEligible&&trialService?`<button id="home-trial" class="trial-card"><strong>Get your free test account</strong><small>Try a limited configuration before you buy. Available once per account.</small></button>`:''}
    <div class="section-heading"><h2>Active services</h2><button class="text-link" data-go="my-services">View all →</button></div>
    ${active.slice(0,2).map(sub=>`<button class="card home-service full" data-go="my-services"><strong>${clean(sub.plan_name)}</strong><span class="status-active">${tr('Active')}</span><p>${remainingTime(sub.expires_at)}</p></button>`).join('')||empty('No active services','Buy your first service to get connected.','Buy your first service','services')}
    ${state.catalog.announcements.map(item=>`<div class="notice"><strong>${clean(item.title)}</strong>${clean(item.body)}</div>`).join('')}`;
}
const purchaseSteps=['Choose category','Choose service','Choose plan','Review & pay','Service ready'];
function purchaseProgress(current,next){return `<section class="purchase-progress" aria-label="${tr('Purchase progress')}"><div class="progress-head"><strong>${tr('Purchase progress')}</strong><span>${window.AlartI18n.number(current)} / ${window.AlartI18n.number(purchaseSteps.length)}</span></div><div class="progress-track">${purchaseSteps.map((label,index)=>`<span class="${index+1<current?'done':index+1===current?'current':''}" title="${tr(label)}"><i>${index+1<current?'✓':window.AlartI18n.number(index+1)}</i><b>${tr(label)}</b></span>`).join('')}</div><p>${tr('Now')}: <strong>${tr(purchaseSteps[current-1])}</strong>${next?` · ${tr('Next')}: <strong>${tr(next)}</strong>`:''}</p></section>`}
function categoryCard(title,description,value,icon){return `<button class="category-card" data-category="${value}"><span class="service-icon">${icon}</span><span><strong>${title}</strong><small>${description}</small></span><b>→</b></button>`}
function services(){return purchaseProgress(1,'Choose service')+heading('Store','Choose a category. We’ll guide you through the details.')+categoryCard('Unlimited configurations','1, 2, 3, or custom concurrent IP limit','unlimited','∞')+categoryCard('Volume configurations','Traffic plans with or without an expiry date','volume','◈')}
function category(){
  return `${purchaseProgress(1,'Choose service')}${backButton('back-services','Services')}${heading('Volume configurations','Choose how your traffic allowance should work.')}${categoryCard('No expiry date','Valid until the traffic allowance is used','volume_unlimited','◌')}${categoryCard('Time limited','Ends when traffic or time runs out','volume_timed','◷')}`;
}
function products(){
  const labels={unlimited:['Unlimited configurations','Choose your user count and plan.'],volume_unlimited:['Volume · no expiry','Your plan lasts until its traffic is consumed.'],volume_timed:['Volume · time limited','Your plan ends when the time or traffic limit is reached.']};
  const [title,subtitle]=labels[state.category]||labels.unlimited;
  const list=state.catalog.services.filter((service)=>service.category===state.category&&!service.trial_enabled),custom=(state.catalog.custom_volume_offers||[]).find(item=>item.category===state.category);
  const special=custom?`<button class="trial-card custom-volume-card" data-custom-volume="${clean(custom.category)}"><span>✨</span><span><strong>${state.category==='volume_unlimited'?'Create your personal configuration':'Create your personal 30-day configuration'}</strong><small>${state.category==='volume_unlimited'?'Choose exactly the GB you need. No expiry date.':'Choose the GB you need for one fixed 30-day period.'}</small></span><b>→</b></button>`:'';
  return `${purchaseProgress(2,'Choose plan')}${backButton('back-products',state.category==='unlimited'?'Services':'Volume plans')}${heading(title,subtitle)}${list.map((service)=>`<button class="category-card" data-service="${service.id}"><span class="service-icon">✦</span><span><strong>${clean(service.name)}</strong><small>${clean(service.description)}</small></span><b>→</b></button>`).join('')}${special||(!list.length?empty('No products available','Please check back later.'):'')}`;
}
function planSummary(plan,isTrial=false){
  const parts=[];
  const fa=window.AlartI18n.language==='fa';
  if(plan.user_limit===0)parts.push(window.AlartI18n.t('Unlimited concurrent IPs'));else if(plan.user_limit)parts.push(fa?`${window.AlartI18n.number(plan.user_limit)} IP هم‌زمان`:`${plan.user_limit} concurrent ${plan.user_limit===1?'IP':'IPs'}`);
  if(plan.traffic_gb)parts.push(fa?`${window.AlartI18n.number(plan.traffic_gb)} گیگابایت`:`${plan.traffic_gb} GB`);else parts.push(window.AlartI18n.t('Unlimited traffic'));
  if(plan.duration_hours){
    parts.push(isTrial?(fa?`${window.AlartI18n.number(plan.duration_hours)} ساعت`:`${plan.duration_hours} hours`):(fa?`${window.AlartI18n.number(Math.ceil(plan.duration_hours/24))} روز`:`${Math.ceil(plan.duration_hours/24)} days`));
  }else parts.push(window.AlartI18n.t('No expiry date'));
  return parts.join(' · ');
}
function product(){
  const service=state.service,plans=state.catalog.plans.filter((plan)=>plan.service_id===service.id),fixed=plans.filter((plan)=>!plan.is_custom),custom=plans.find((plan)=>plan.is_custom);
  return `${purchaseProgress(3,'Review & pay')}${backButton('back-list','Products')}${heading(clean(service.name),clean(service.description))}
    <p class="eyebrow">QUICK PICKS</p>${fixed.map((plan)=>`<button class="plan-option" data-plan="${plan.id}"><span><strong>${clean(plan.name)}</strong><small>${planSummary(plan,service.trial_enabled)}</small></span><span class="plan-price">${tr('View price')}<b>→</b></span></button>`).join('')||empty('No preset plans','Ask the operator to add a plan.')}
    ${custom?`<div class="section-heading"><h2>Make it yours</h2></div><button class="category-card" data-plan="${custom.id}"><span class="service-icon">＋</span><span><strong>Custom configuration</strong><small>Choose concurrent IPs, traffic, and duration</small></span><b>→</b></button>`:''}
    <p class="muted">The limit counts concurrent IP addresses. It does not bind a configuration to physical devices.</p>
    ${service.trial_enabled?`<div class="section-heading"><h2>Try Alart</h2></div>${button('Request a limited test account','trial','ghost')}`:''}`;
}
function builder(){
  const plan=state.plan,custom=plan.is_custom,limits=state.limits;
  return `${purchaseProgress(4,'Service ready')}${backButton('back-plan',clean(state.service.name))}${heading(clean(plan.name),custom?'Make a plan that fits you.':'Review your plan before secure payment.')}
    <div class="step-label">01 · CONFIGURATION</div>
    ${custom&&state.service.category==='unlimited'?`<label class="field">Concurrent IP limit<input id="users" type="number" inputmode="numeric" min="${limits.min_users}" max="${limits.max_users}" value="${limits.min_users}">${limits.min_users===0?'<small>0 = Unlimited</small>':''}</label>`:`<div class="selection-summary">${planSummary(plan,state.service.trial_enabled)}</div>`}
    ${custom&&state.service.category.startsWith('volume')?`<label class="field">Traffic allowance (GB)<input id="traffic" type="number" inputmode="numeric" min="${limits.min_traffic_gb}" max="${limits.max_traffic_gb}" value="${limits.min_traffic_gb}"></label>`:''}
    ${custom&&state.service.category!=='volume_unlimited'&&!state.customFixedDays?`<label class="field">Duration (days)<input id="days" type="number" inputmode="numeric" min="${limits.min_days}" max="${limits.max_days}" value="${limits.min_days}"></label>`:''}
    <div class="step-label">02 · DELIVERY</div><div class="selection-summary"><strong>${tr('Automatic routing')}</strong><small class="muted">${tr('We choose a healthy compatible route automatically. Operational panel names are not shown to customers.')}</small></div>
    <div class="step-label">03 · PAYMENT</div><label class="field">Payment method<select id="payment-method"><option value="stars">Telegram Stars</option>${(state.catalog.web_payment_methods||[]).includes('manual')?'<option value="manual">Card transfer</option>':''}</select></label><div class="step-label">04 · PRICE</div><label class="field">Coupon code (optional)<input id="coupon" autocomplete="off" placeholder="Add a code" value="${clean(state.promoCoupon||'')}"></label>
    <div id="quote-card" class="card"><div class="skeleton line"></div></div>${button('Continue','continue')}`;
}
function wallet(){
  return `${heading('Wallet','Your balance and transaction history.')}<div class="hero wallet-hero wallet-page-hero"><p>AVAILABLE BALANCE</p><h1>${toman(state.me.balance_minor)}</h1>${button('Top up wallet','wallet-topup')}</div>
    <p class="muted wallet-note">Wallet funding is available only after the operator enables a verified payment method.</p><div class="section-heading"><h2>Transactions</h2></div>${state.account.transactions.map((item)=>`<div class="card"><div class="row"><strong>${clean(item.kind)}</strong><strong>${item.amount_minor>0?'+':''}${toman(item.amount_minor)}</strong></div><small>${window.AlartI18n.date(item.created_at)}</small></div>`).join('')||empty('No transactions yet','Your wallet activity will appear here.',null)}`;
}
function serviceStatus(sub){
  if(['active','expiring'].includes(sub.status)&&((sub.expires_at&&Date.parse(sub.expires_at)<=Date.now())||(sub.traffic_limit_bytes>0&&sub.traffic_used_bytes!=null&&sub.traffic_used_bytes>=sub.traffic_limit_bytes)))return 'expired';
  return sub.status;
}
function remainingTime(expiry){
  if(!expiry)return tr('No expiry date');
  const minutes=Math.ceil((Date.parse(expiry)-Date.now())/60000);
  if(minutes<=0)return tr('Expired');
  const days=Math.floor(minutes/1440),hours=Math.floor(minutes%1440/60),mins=minutes%60;
  const parts=[];
  if(days)parts.push(`${money(days)} ${tr('days')}`);
  if(hours)parts.push(`${money(hours)} ${tr('hours')}`);
  if(!days&&!hours)parts.push(`${money(mins)} ${tr('minutes')}`);
  return parts.join(' · ')+' '+tr('remaining');
}
function myServices(){
  const subscriptions=state.account?.subscriptions||[];
  const pending=(state.account?.orders||[]).filter(order=>['awaiting_payment','manual_review','paid','provisioning'].includes(order.status)&&!subscriptions.some(sub=>sub.order_id===order.id));
  return `${heading('My services','Manage your connection, remaining allowance and renewal.')}<button class="ghost full" id="refresh-services">Refresh services</button>
  ${pending.map(order=>`<section class="card service-card pending-card"><div class="row"><strong>${clean(order.plan)} #${order.id}</strong><span class="chip status-pending">${status(order.status)}</span></div><p>${tr(order.status==='awaiting_payment'?'Your order is ready and waiting for payment.':'Your payment or service is being processed.')}</p>${(order.status==='awaiting_payment'||(order.payment_method==='manual'&&order.status==='manual_review'&&(order.review_status==='more_info'||!order.has_receipt)))?`<button class="primary full" data-resume-order="${order.id}">${tr(order.status==='manual_review'?'Submit receipt':'Pay and receive service')}</button>`:''}</section>`).join('')}
  ${subscriptions.map(sub=>{const current=serviceStatus(sub),ended=current==='expired',active=['active','expiring'].includes(current);return `<section class="card service-card ${ended?'expired-card':''}"><div class="row"><strong>${clean(sub.plan_name||'Configuration')} #${sub.id}</strong><span class="chip status-${ended?'expired':active?'active':'pending'}">${status(current)}</span></div>
  ${ended?`<div class="expired-summary"><div class="traffic-ring expired-ring" aria-label="${tr('Expired')}"><strong>${tr('Expired')}</strong></div><div class="expired-copy"><h2>Service ended</h2><p>${tr(sub.remote_missing_at?'This service was removed from the panel.':'Your time or traffic allowance has ended.')}</p></div></div>`:active?`<div class="service-limits"><div><span>Time remaining</span><strong>${remainingTime(sub.expires_at)}</strong></div><div><span>Concurrent IP limit</span><strong>${sub.user_limit?money(sub.user_limit):tr('Unlimited')}</strong></div></div>${usageSummary(sub)}${connectionHelp(sub)}`:`<p>${status(current)}</p>`}
  ${!sub.is_trial&&['active','expiring','expired'].includes(current)&&!sub.remote_missing_at&&(sub.expires_at||sub.category==='volume_unlimited')?`<button class="primary full renew-button" data-renew="${sub.id}">Renew service</button>`:''}
  ${ended?`<div class="actions">${sub.is_trial||sub.remote_missing_at?'<button class="primary full" data-go="services">Buy service</button>':''}<button class="ghost full" data-archive="${sub.id}">Remove from my services</button></div>`:''}
  <button class="text-link full" data-go="guide">Connection guide</button></section>`}).join('')||(!pending.length?empty('No subscriptions','Buy your first service to get connected.','Buy your first service','services'):'')}`;
}
function connectionHelp(sub){
  const url=/^https:\/\//.test(sub.provider_subscription_url||'')?sub.provider_subscription_url:'';
  return `<div class="connection-details">${url?`<div class="subscription-link"><a href="${clean(url)}" target="_blank" rel="noopener noreferrer" dir="ltr">${clean(url)}</a><button class="primary" data-subscription-copy="${sub.id}">Copy</button></div><details data-qr-details="${sub.id}"><summary>Show QR code</summary><button class="qr-copy" data-subscription-copy="${sub.id}" aria-label="Copy subscription link"><img data-qr-src="/api/subscriptions/${sub.id}/qr" alt="QR code for subscription link"><span>Tap QR to copy subscription link</span></button></details>`:'<p class="muted">Subscription URL is not available from the panel yet</p>'}<button class="ghost full" data-subscription-show="${sub.id}">Refresh from panel</button>${sub.configuration?`<details><summary>Individual connection links</summary>${sub.configuration.split(/\r?\n/).filter(Boolean).map((link,index)=>`<button class="connection-copy" data-connection="${sub.id}" data-index="${index}"><code dir="ltr">${clean(link)}</code><span>Copy</span></button>`).join('')}</details>`:''}</div>`;
}
async function loadGuides(){try{state.guides=await api('guides');if(state.page==='guide')render()}catch(error){toast(error.message)}}
function guide(){
  const platform=state.guidePlatform||'android',item=state.guides?.find(item=>item.platform===platform);
  return `${heading('Connection guide','Choose your device for connection instructions.')}<div class="platform-tabs">${[['ios','iOS'],['android','Android'],['windows','Windows'],['macos','macOS']].map(([key,label])=>`<button class="${platform===key?'primary':'ghost'}" data-platform="${key}">${label}</button>`).join('')}</div>${item?`<section class="card"><h2>${clean(item.title)}</h2><p class="guide-body">${clean(item.body)}</p>${item.video_key?`<video controls playsinline preload="none" src="/api/guides/video/${encodeURIComponent(item.video_key)}"></video>`:''}</section>`:empty(state.guides?'Guide coming soon':'Loading…','Contact support for connection help.','Contact support','support')}<button class="ghost full" data-go="my-services">My services</button>`;
}
function formatBytes(bytes){const value=Number(bytes);return value<1024**3?`${window.AlartI18n.number(Math.round(value/1024**2))} MB`:`${window.AlartI18n.number(Math.round(value/1024**3*10)/10)} GB`}
function usageSummary(sub){
  const limit=Number(sub.traffic_limit_bytes||0),used=sub.traffic_used_bytes;
  if(limit<=0)return '<div class="row"><span>Usage</span><strong>Unlimited traffic</strong></div>';
  if(used===null||used===undefined)return '<div class="row"><span>Usage</span><strong>Not available yet</strong></div>';
  const safeUsed=Math.max(0,Number(used)||0),percent=Math.min(100,Math.round(safeUsed/limit*100)),old=sub.traffic_sampled_at&&Date.now()-new Date(sub.traffic_sampled_at).getTime()>600000;
  return `<div class="traffic-summary"><div class="traffic-ring" style="--usage:${percent}%"><span>${percent}%</span></div><div><p><strong>${formatBytes(Math.max(0,limit-safeUsed))}</strong> remaining</p><p class="muted">${formatBytes(safeUsed)} ${tr("used of")} ${formatBytes(limit)}</p><small class="muted">${old?'Last measured':'Measured'}: ${window.AlartI18n.date(sub.traffic_sampled_at)}</small></div></div>`;
}
async function refreshUsage(id){
  try{const result=await api(`subscriptions/${id}/usage`),sub=state.account.subscriptions.find((item)=>item.id===id);if(!sub)return;sub.traffic_used_bytes=result.used_bytes;sub.traffic_sampled_at=result.sampled_at;if(state.page==='my-services')render();if(!result.available)toast('Usage is unavailable from the panel right now')}
  catch(error){toast(error.message)}
}
function account(){
  const name=clean(state.me.name||'Alart customer'),username=state.me.username?`@${clean(state.me.username)}`:tr('Telegram account'),avatar=/^https:\/\//.test(state.me.photo_url||'')?'<img src="/api/me/avatar" alt="">':clean((state.me.name||'A').slice(0,1).toUpperCase());
  const active=state.account.subscriptions.filter((sub)=>sub.status==='active').length,orders=state.account.orders.length,ref=state.referral;
  const referralText=ref?`${clean(ref.code)} · ${window.AlartI18n.number(ref.successful)} successful`:'Create your personal invite link and earn rewards after a verified first purchase.';
  return `${heading('Your account','Your private profile, services, and preferences.')}
    <section class="card profile-card"><div class="profile-avatar">${avatar}</div><div><h2>${name}</h2><p class="muted">${username}</p><small>Joined ${state.me.created_at?window.AlartI18n.date(state.me.created_at):'—'}</small></div></section>
    <div class="account-stats"><div class="account-stat"><small>Wallet</small><strong>${toman(state.me.balance_minor)}</strong></div><div class="account-stat"><small>Active services</small><strong>${window.AlartI18n.number(active)}</strong></div><div class="account-stat"><small>Orders</small><strong>${window.AlartI18n.number(orders)}</strong></div></div>
    <button id="referrals" class="referral-hero"><span>🎁</span><span><strong>Invite friends, earn rewards</strong><small>${referralText}</small></span><b>›</b></button>
    <div class="section-heading"><h2>Profile</h2></div><div class="card"><div class="row"><span>Telegram ID</span><strong>${state.me.telegram_id}</strong></div><div class="row"><span>Referral code</span><strong>${ref?clean(ref.code):'Loading…'}</strong></div></div>
    <div class="section-heading"><h2>Appearance</h2></div><div class="card"><label class="field">Language<select id="language"><option value="fa">فارسی</option><option value="en">English</option></select></label><label class="field">Theme<select id="theme"><option value="system">System</option><option value="dark">Dark</option><option value="light">Light</option></select></label></div>
    `;
}
function referrals(){
  const data=state.referral;
  if(!data)return heading('Refer friends','Rewards follow a verified first purchase and successful provisioning.')+'<div class="skeleton card"></div>';
  if(data.error)return heading('Refer friends','Rewards follow a verified first purchase and successful provisioning.')+empty('Unable to load referrals',clean(data.error))+button('Try again','referral-retry','ghost');
  return `${heading('Refer friends','Rewards follow a verified first purchase and successful provisioning.')}
    <div class="card"><div class="row"><span>Your code</span><strong>${clean(data.code)}</strong></div><div class="row"><span>Invited users</span><strong>${window.AlartI18n.number(data.invited)}</strong></div><div class="row"><span>Successful referrals</span><strong>${window.AlartI18n.number(data.successful)}</strong></div><div class="row"><span>Referral earnings</span><strong>${toman(data.total_earnings_minor)}</strong></div><div class="row"><span>Wallet available</span><strong>${toman(data.wallet_balance_minor)}</strong></div></div>
    ${data.link?`<div class="card"><div class="row"><span>Referral link</span><strong class="break-word">${clean(data.link)}</strong></div><div class="actions">${button('Copy link','copy-referral','ghost')}${button('Share in Telegram','share-referral','primary')}</div></div>`:empty('Link unavailable','The bot link could not be verified right now. Try again later.')}`;
}
async function loadReferrals(){try{state.referral=await api('referrals/me')}catch(error){state.referral={error:error.message}}if(['referrals','account'].includes(state.page))render()}
function support(){
  const contact=state.supportContact,operator=contact?(contact.username?`@${clean(contact.username)}`:clean(contact.name||'Support')):null;
  return `${backButton('back-support','Home')}${heading('Support','Send a message to the support operator from this secure page.')}${contact?`<section class="card support-card"><div class="row"><strong>Message ${operator}</strong><span class="chip">Private</span></div><p class="muted">Your Telegram ID is included so the operator can reply. Do not send connection links, passwords, or payment secrets.</p><form id="support-form"><label class="field">How can we help?<textarea id="support-message" maxlength="1500" required placeholder="Describe the issue, service, or payment step."></textarea></label><button class="primary full">Send to support</button></form></section>`:empty('Support is not configured','Please use the bot owner contact until a support operator is configured.')}`;
}
function order(){return `${heading('Payment status','Your order is being processed.')}<div class="card"><div class="row"><span>Order</span><strong>#${state.orderId}</strong></div><div class="row"><span>Status</span><strong id="order-status">Checking…</strong></div></div>${button('View account','view-account','ghost')}`}

let quoteSequence=0;
async function quote(){
  const sequence=++quoteSequence;state.quote=null;$('#continue').disabled=true;
  const plan=state.plan, selection={plan_id:plan.id,payment_method:state.paymentMethod,coupon_code:$('#coupon').value.trim()||null};
  if(plan.is_custom){if($('#users'))selection.users=Number($('#users').value);if($('#traffic'))selection.traffic_gb=Number($('#traffic').value);if(state.customFixedDays)selection.duration_days=state.customFixedDays;else if($('#days'))selection.duration_days=Number($('#days').value)}
  try{
    const result=await post('quote',selection);if(sequence!==quoteSequence||state.page!=='builder')return;state.quote={...result,selection:{...selection}};
    const tomanPrice=value=>toman(value);const paymentPrice=value=>displayMoney(value,result.currency||(selection.payment_method==='stars'?'XTR':'IRR'));
    const listedToman=result.toman_listed_minor??result.listed_minor,discountToman=result.toman_discount_minor??result.discount_minor,finalToman=result.toman_final_minor??result.final_minor;
    const starsPayment=selection.payment_method==='stars'?`<div class="row"><span>${tr('Pay with Telegram Stars')}</span><strong>${paymentPrice(result.final_minor)}</strong></div><p class="muted">${tr('The Toman price above is converted to Stars by Telegram at the current verified rate.')}</p>`:'';
    $('#quote-card').innerHTML=`<div class="row"><span>Plan</span><strong>${clean(plan.name)}</strong></div><div class="row"><span>Listed price</span><strong>${tomanPrice(listedToman)}</strong></div><div class="row"><span>Discount</span><strong>− ${tomanPrice(discountToman)}</strong></div><div class="row total"><span>Final amount</span><strong>${tomanPrice(finalToman)}</strong></div>${starsPayment}${result.pricing_reference?`<p class="muted">${tr('Calculated from the current Toman reference')} · ${clean(result.pricing_reference.updated_at||'')}</p>`:''}`;
    $('#continue').disabled=selection.payment_method==='stars'&&!state.catalog.stars_enabled;
    if(selection.payment_method==='stars'&&!state.catalog.stars_enabled)$('#quote-card').insertAdjacentHTML('beforeend','<p class="muted">Stars payments are currently unavailable.</p>');
  }catch(error){if(sequence!==quoteSequence||state.page!=='builder')return;state.quote=null;$('#quote-card').innerHTML=`<p class="error-text">${clean(error.message)}</p>`;$('#continue').disabled=true}
}
async function startCheckout(){
  if(!state.quote)return;
  const q=state.quote,location=state.catalog.locations[0];
  const tomanFinal=q.toman_final_minor??q.final_minor,starsNote=q.selection.payment_method==='stars'?` · ${tr('Pay with Telegram Stars')}: ${displayMoney(q.final_minor,q.currency||'XTR')}`:'';
  $('#confirm-text').textContent=`${state.service.name} · ${toman(tomanFinal)}${starsNote}. ${tr('Your service activates after payment verification and successful provisioning.')}`;
  $('#confirm-buy').disabled=false;
  $('#confirm').showModal();
  $('#confirm-buy').onclick=async(event)=>{
    event.preventDefault();
    const confirmButton=$('#confirm-buy');
    if(confirmButton.disabled)return;
    confirmButton.disabled=true;
    try{
      state.checkoutKey ||= crypto.randomUUID();
      if(!location)throw Error(tr('No delivery route is available right now'));
      const body={...q.selection,location_id:location.id,expected_final_minor:q.final_minor,request_key:state.checkoutKey};
      const created=await post('orders',body); state.orderId=created.id; if(q.selection.payment_method==='manual'){const details=await api(`payments/manual/${created.id}/instructions`);state.topup={id:null,orderId:created.id,amount_minor:details.amount_minor,destination:details.destination,orderManual:true};$('#confirm').close();navigate('manual-order');return}$('#confirm').close();const invoice=await post(`payments/stars/${created.id}/invoice`,{}); if(!tg?.openInvoice)throw Error('Telegram invoice is unavailable on this device'); tg.openInvoice(invoice.invoice_url,async()=>{await refresh();navigate('order');pollOrder(created.id)});
    }catch(error){toast(error.message);confirmButton.disabled=false}
  };
}
async function pollOrder(orderId){
  for(let attempt=0;attempt<18;attempt++){
    try{const item=await api(`orders/${orderId}/status`);if($('#order-status'))$('#order-status').textContent=status(item.status)+(item.refund_status?' · '+tr('Refund')+': '+status(item.refund_status):'');if(['active','failed','refunded','cancelled','provisioning_failed'].includes(item.status)){await refresh();if(item.status==='active')navigate('my-services');return}}catch(error){toast(error.message);return}
    await new Promise((resolve)=>setTimeout(resolve,5000));
  }
}
async function startTrial(){
  const location=state.catalog.locations[0];if(!location){toast('No location is available');return}
  try{const created=await post('trials',{service_id:state.service.id,location_id:location.id});state.trialEligible=false;await refresh();toast('Test configuration is being provisioned');state.orderId=created.id;navigate('order');pollOrder(created.id)}catch(error){toast(error.message)}
}
async function openRenewal(id){
  try{state.renewal=await api(`subscriptions/${id}/renewal-options?payment_method=stars`);state.renewalChoice=state.renewal.options[0];state.renewalKey=null;navigate('renewal')}
  catch(error){toast(error.message);navigate('my-services')}
}
function renewal(){
  const info=state.renewal,chosen=state.renewalChoice;
  if(!info)return empty('Renewal unavailable','Select an existing service first.');
  return `${heading('Renew this service','The existing configuration and service identity are kept when the panel supports renewal.')}
    ${backButton('back-renewal','My services')}
    <div class="card"><div class="row"><span>Current status</span><strong>${clean(status(info.status))}</strong></div>
    <div class="row"><span>Current expiry</span><strong>${info.current_expires_at?window.AlartI18n.date(info.current_expires_at):tr('No expiry date')}</strong></div>
    <div class="row"><span>Current traffic limit</span><strong>${info.current_traffic_limit_bytes?formatBytes(info.current_traffic_limit_bytes):tr('Unlimited traffic')}</strong></div></div>
    <div class="section-heading"><h2>Choose renewal</h2></div>
    ${info.options.map((item)=>`<button class="plan-option ${item.plan_id===chosen?.plan_id?'selected':''}" data-renew-plan="${item.plan_id}"><span><strong>${clean(item.plan_name)}</strong><small>${item.renewal_mode==='traffic_topup'?tr('Traffic top-up'):item.renewal_mode==='time_and_traffic'?tr('Time and traffic'):tr('Time extension')} · ${item.duration_hours?window.AlartI18n.number(item.duration_hours/24)+' '+tr('days'):tr('No time limit')}${item.added_traffic_bytes?' · +'+formatBytes(item.added_traffic_bytes):''}</small></span><strong>${displayMoney(item.price_minor,item.currency)}</strong></button>`).join('')}
    ${chosen?`<div class="card"><div class="row"><span>New expiry</span><strong>${chosen.target_expires_at?window.AlartI18n.date(chosen.target_expires_at):tr('No expiry date')}</strong></div>
      <div class="row"><span>New traffic limit</span><strong>${chosen.target_traffic_limit_bytes?formatBytes(chosen.target_traffic_limit_bytes):tr('Unlimited traffic')}</strong></div>
      <div class="row total"><span>Price</span><strong>${displayMoney(chosen.price_minor,chosen.currency)}</strong></div></div>${button('Continue to payment','renew-pay')}`:''}`;
}
async function startRenewalCheckout(){
  const info=state.renewal,choice=state.renewalChoice,button=$('#renew-pay');if(!info||!choice||button?.disabled)return;
  if(!tg?.openInvoice){toast('Telegram invoice is unavailable on this device');return}
  if(!window.confirm(tr('Confirm renewal of this existing service?')))return;
  button.disabled=true;
  state.renewalKey ||= crypto.randomUUID();
  try{const created=await post(`subscriptions/${info.subscription_id}/renew`,{request_key:state.renewalKey,payment_method:'stars',plan_id:choice.plan_id,expected_final_minor:choice.price_minor});
    const invoice=await post(`payments/stars/${created.id}/invoice`,{});state.orderId=created.id;
    tg.openInvoice(invoice.invoice_url,async()=>{await refresh();navigate('order');pollOrder(created.id)})
  }catch(error){button.disabled=false;toast(error.message)}
}

async function copyText(value){
  try{await navigator.clipboard.writeText(value)}catch{
    const field=document.createElement('textarea');field.value=value;field.style.cssText='position:fixed;top:0;opacity:0';document.body.append(field);field.focus();field.select();field.setSelectionRange(0,value.length);
    const copied=document.execCommand('copy');field.remove();if(!copied)throw Error(tr('Copy unavailable'));
  }
}
async function resumeOrder(id){
  const item=state.account.orders.find(item=>item.id===id);if(!item)return;
  state.orderId=id;
  try{if(item.payment_method==='stars'){const invoice=await post(`payments/stars/${id}/invoice`,{});if(!tg?.openInvoice)throw Error('Telegram invoice is unavailable on this device');tg.openInvoice(invoice.invoice_url,async()=>{await refresh();navigate('order');pollOrder(id)})}else if(item.payment_method==='manual'){const details=await api(`payments/manual/${id}/instructions`);state.topup={orderId:id,amount_minor:details.amount_minor,destination:details.destination,orderManual:true};navigate('manual-order')}else{navigate('order');pollOrder(id)}}catch(error){toast(error.message)}
}
function bind(){
  if($('#manual-order-receipt'))$('#manual-order-receipt').onsubmit=submitManualOrder;
  if($('#back-manual'))$('#back-manual').onclick=()=>navigate('my-services');
  document.querySelectorAll('[data-go]').forEach(button=>button.onclick=()=>navigate(button.dataset.go));
  document.querySelectorAll('[data-platform]').forEach(button=>button.onclick=()=>{state.guidePlatform=button.dataset.platform;render()});
  document.querySelectorAll('[data-resume-order]').forEach(button=>button.onclick=()=>resumeOrder(Number(button.dataset.resumeOrder)));
  document.querySelectorAll('[data-qr-details]').forEach(node=>node.ontoggle=()=>{const img=node.querySelector('img');if(node.open&&img&&!img.src)img.src=img.dataset.qrSrc});
  document.querySelectorAll('[data-connection]').forEach(button=>button.onclick=async()=>{const sub=state.account.subscriptions.find(sub=>sub.id===Number(button.dataset.connection));try{await copyText(sub.configuration.split(/\r?\n/).filter(Boolean)[Number(button.dataset.index)]);toast('Connection links copied')}catch(error){toast(error.message)}});
  document.querySelectorAll('[data-archive]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{await post(`subscriptions/${button.dataset.archive}/archive`,{});await refresh();render()}catch(error){button.disabled=false;toast(error.message)}});
  if($('#refresh-services'))$('#refresh-services').onclick=async()=>{try{await refresh();render()}catch(error){toast(error.message)}};

  if($('#notifications'))$('#notifications').onclick=()=>navigate('notifications');if($('#back-notifications'))$('#back-notifications').onclick=()=>navigate('home');document.querySelectorAll('[data-notification-action]').forEach((button)=>button.onclick=()=>{const match=String(button.dataset.notificationAction).match(/^\/app\/\?renew=(\d+)$/);if(match)openRenewal(Number(match[1]));else if(button.dataset.notificationAction==='/app/?page=notifications')navigate('my-services')});
  if($('#explore'))$('#explore').onclick=()=>navigate('services');if($('#all-services'))$('#all-services').onclick=()=>navigate('services');if($('#home-trial'))$('#home-trial').onclick=()=>{state.service=state.catalog.services.find((service)=>service.trial_enabled);startTrial()};
  document.querySelectorAll('[data-category]').forEach((button)=>button.onclick=()=>{state.category=button.dataset.category;navigate(state.category==='volume'?'category':'products')});
  if($('#back-services'))$('#back-services').onclick=()=>navigate('services');
  if($('#back-products'))$('#back-products').onclick=()=>navigate(state.category==='unlimited'?'services':'category');
  document.querySelectorAll('[data-service]').forEach((button)=>button.onclick=()=>{state.customFixedDays=null;state.service=state.catalog.services.find((item)=>item.id===Number(button.dataset.service));navigate('product')});
  document.querySelectorAll('[data-custom-volume]').forEach((button)=>button.onclick=()=>{const offer=(state.catalog.custom_volume_offers||[]).find(item=>item.category===button.dataset.customVolume);if(!offer)return;state.service={id:offer.service_id,name:offer.category==='volume_unlimited'?'Personal volume configuration':'Personal 30-day configuration',description:offer.category==='volume_unlimited'?'Choose your own traffic amount with no expiry date.':'Choose your traffic amount for 30 days.',category:offer.category};state.plan={...offer,id:offer.plan_id};state.limits={min_users:1,max_users:1,min_traffic_gb:1,max_traffic_gb:500,min_days:1,max_days:30};state.customFixedDays=offer.category==='volume_timed'?30:null;state.checkoutKey=null;navigate('builder')});
  if($('#back-list'))$('#back-list').onclick=()=>navigate('products');
  document.querySelectorAll('[data-plan]').forEach((button)=>button.onclick=async()=>{state.plan=state.catalog.plans.find((item)=>item.id===Number(button.dataset.plan));state.checkoutKey=null;try{state.limits=state.plan.is_custom?await api(`services/${state.service.id}/custom-limits`):null;navigate('builder')}catch(error){toast(error.message)}});
  if($('#back-plan'))$('#back-plan').onclick=()=>navigate('product');
  if($('#quote-card')){if($('#payment-method')){$('#payment-method').value=state.paymentMethod;$('#payment-method').onchange=(event)=>{state.paymentMethod=event.target.value;state.checkoutKey=null;quote()}}['users','traffic','days','coupon'].forEach((id)=>{const node=$('#'+id);if(node)node.onchange=()=>{state.checkoutKey=null;quote()}});quote();$('#continue').onclick=startCheckout}
  if($('#trial'))$('#trial').onclick=startTrial;
  if($('#support'))$('#support').onclick=()=>navigate('support');if($('#back-support'))$('#back-support').onclick=()=>navigate('home');if($('#support-form'))$('#support-form').onsubmit=async(event)=>{event.preventDefault();const body=$('#support-message').value.trim();if(!body)return;const submit=event.target.querySelector('button');submit.disabled=true;try{await post('support/requests',{body});$('#support-message').value='';toast('Your message was sent to support')}catch(error){toast(error.message)}finally{submit.disabled=false}};
  if($('#referrals'))$('#referrals').onclick=()=>navigate('referrals');
  if($('#referral-retry'))$('#referral-retry').onclick=()=>{state.referral=null;render();loadReferrals()};
  if($('#copy-referral'))$('#copy-referral').onclick=async()=>{try{await navigator.clipboard.writeText(state.referral.link);toast('Referral link copied')}catch{toast('Copy unavailable')}};
  if($('#share-referral'))$('#share-referral').onclick=()=>{const link=state.referral.link;if(!link)return;const share=`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent('Join Alart')}`;if(tg?.openTelegramLink)tg.openTelegramLink(share);else window.open(share,'_blank','noopener')};
  if($('#wallet-topup'))$('#wallet-topup').onclick=()=>{state.topup=null;navigate('topup')};if($('#back-wallet'))$('#back-wallet').onclick=()=>navigate('wallet');if($('#topup-form'))$('#topup-form').onsubmit=(event)=>createTopup(event).catch(error=>toast(error.message));if($('#topup-receipt'))$('#topup-receipt').onsubmit=submitTopup;if($('#topup-change'))$('#topup-change').onclick=()=>{state.topup=null;navigate('topup')};if($('#copy-card'))$('#copy-card').onclick=async()=>{try{await navigator.clipboard.writeText(state.topup.destination);toast('Card number copied')}catch{toast('Copy unavailable')}};if($('#topup-manual'))$('#topup-manual').onclick=chooseManualTopup;if($('#topup-stars'))$('#topup-stars').onclick=chooseStarsTopup;
  if($('#theme')){$('#theme').value=localStorage.getItem('alart-theme')||'system';$('#theme').onchange=(event)=>setTheme(event.target.value)}
  if($('#language')){$('#language').value=window.AlartI18n.language;$('#language').onchange=async(event)=>{const language=event.target.value;try{await post('me/language',{language});state.me.language=language;state.guides=null;window.AlartI18n.setLanguage(language);render()}catch(error){toast(error.message)}}}
  document.querySelectorAll('[data-copy]').forEach((button)=>button.onclick=async()=>{const value=state.account.subscriptions.find((sub)=>sub.id===Number(button.dataset.copy))?.configuration;if(!value)return;try{await navigator.clipboard.writeText(value);toast('Connection links copied')}catch{toast('Copy unavailable')}});
  document.querySelectorAll('[data-subscription-show]').forEach((button)=>button.onclick=async()=>{const sub=state.account.subscriptions.find(item=>item.id===Number(button.dataset.subscriptionShow));if(!sub)return;button.disabled=true;try{const result=await post(`subscriptions/${sub.id}/connection-links/refresh`,{});sub.configuration=result.configuration;sub.provider_subscription_url=result.provider_subscription_url;render()}catch(error){button.disabled=false;toast(error.message)}});
  document.querySelectorAll('[data-subscription-copy]').forEach((button)=>button.onclick=async()=>{const sub=state.account.subscriptions.find(item=>item.id===Number(button.dataset.subscriptionCopy));if(!sub||!sub.provider_subscription_url)return;try{await copyText(sub.provider_subscription_url);toast('Subscription link copied')}catch(error){toast(error.message)}});
  document.querySelectorAll('[data-copy-one]').forEach((button)=>button.onclick=async()=>{const value=state.account.subscriptions.find((sub)=>sub.id===Number(button.dataset.copyOne))?.configuration?.split(/\r?\n/).find(Boolean);if(!value)return;try{await navigator.clipboard.writeText(value.trim());toast('Connection links copied')}catch{toast('Copy unavailable')}});
  document.querySelectorAll('[data-hiddify]').forEach((button)=>button.onclick=()=>{const value=state.account.subscriptions.find((sub)=>sub.id===Number(button.dataset.hiddify))?.configuration?.trim();if(!value||value.includes('\n'))return;window.location.href=`hiddify://import/${value}`;toast('If the app did not open, copy and import the link manually')});
  document.querySelectorAll('[data-usage]').forEach((button)=>button.onclick=()=>refreshUsage(Number(button.dataset.usage)));
  document.querySelectorAll('[data-refresh-links]').forEach((button)=>button.onclick=async()=>{const id=Number(button.dataset.refreshLinks);button.disabled=true;try{const result=await post(`subscriptions/${id}/connection-links/refresh`,{}),sub=state.account.subscriptions.find((item)=>item.id===id);if(sub)sub.configuration=result.configuration;render();toast('Connection links refreshed')}catch(error){toast(error.message);button.disabled=false}});
  document.querySelectorAll('[data-cancel-order]').forEach((button)=>button.onclick=async()=>{const id=Number(button.dataset.cancelOrder);if(!window.confirm(tr('Request cancellation for this order?')))return;try{await post(`orders/${id}/cancellation`,{reason:'Customer requested cancellation'});await refresh();render();toast('Cancellation request recorded')}catch(error){toast(error.message)}});
  if($('#back-renewal'))$('#back-renewal').onclick=()=>navigate('my-services');
  document.querySelectorAll('[data-renew-plan]').forEach((button)=>button.onclick=()=>{state.renewalChoice=state.renewal.options.find((item)=>item.plan_id===Number(button.dataset.renewPlan));state.renewalKey=null;render()});
  if($('#renew-pay'))$('#renew-pay').onclick=startRenewalCheckout;
  document.querySelectorAll('[data-renew]').forEach((button)=>button.onclick=()=>openRenewal(Number(button.dataset.renew)));
  if($('#view-account'))$('#view-account').onclick=()=>navigate('account');
  if($('#chat'))$('#chat').onsubmit=async(event)=>{event.preventDefault();const question=$('#question').value.trim();if(!question)return;$('#question').value='';$('#messages').insertAdjacentHTML('beforeend',`<div class="message user">${clean(question)}</div>`);try{const reply=await post('assistant',{question});$('#messages').insertAdjacentHTML('beforeend',`<div class="message">${clean(reply.answer)}</div>`)}catch(error){toast(error.message)}};
}
boot();

function topup(){
  const current=state.topup;
  if(!current)return `${backButton('back-wallet','Wallet')}${heading('Top up wallet','Choose the amount first, then select your preferred payment method.')}<form id="topup-form"><label class="field">Amount (Toman)<input id="topup-amount" type="number" inputmode="numeric" min="1000" max="1000000000" required></label><p class="muted">You will see the exact payment amount before any payment starts.</p><button class="primary full">Continue</button></form>`;
  if(current.choosing)return `${backButton('back-wallet','Wallet')}${heading('Choose payment method',`Top up ${toman(current.amount_minor)} using a secure method.`)}<div class="actions">${button('Telegram Stars','topup-stars')}${button('Card transfer','topup-manual','ghost')}</div><p class="muted">Stars are confirmed by Telegram. Card transfers are reviewed by an operator after you upload a receipt.</p>`;
  return `${backButton('back-wallet','Wallet')}${heading('Card transfer','Transfer exactly the requested amount, then submit a receipt for review.')}<div class="card"><div class="row"><span>Amount</span><strong>${toman(current.amount_minor)}</strong></div><div class="row"><span>Card / destination</span><strong class="break-word">${clean(current.destination)}</strong></div><button class="ghost full" type="button" id="copy-card">Copy card number</button></div><form id="topup-receipt"><label class="field">Payment reference <small>(optional)</small><input id="topup-reference" maxlength="200" value="${clean(current.customer_reference||'')}"></label><label class="field">Receipt image <input id="topup-file" type="file" accept="image/jpeg,image/png,image/webp" required></label><p class="muted">JPEG, PNG, or WebP only, up to 3 MB. A receipt is reviewed by an operator and never approved automatically.</p><div class="actions">${button('Submit receipt','topup-submit')}${button('Change amount','topup-change','ghost')}</div></form>`;
}

async function createTopup(event){event.preventDefault();const amount=Number($('#topup-amount').value);if(!Number.isInteger(amount)||amount<1000)throw Error('Enter a valid amount');state.topup={amount_minor:amount*10,choosing:true};navigate('topup')}
async function chooseManualTopup(){try{const result=await post('wallet/topups/manual',{amount_minor:state.topup.amount_minor,request_key:crypto.randomUUID()});state.topup=result;navigate('topup')}catch(error){toast(error.message)}}
async function chooseStarsTopup(){try{const result=await post('wallet/topups/stars',{amount_minor:state.topup.amount_minor,request_key:crypto.randomUUID()});if(!tg?.openInvoice)throw Error('Telegram invoice is unavailable on this device');tg.openInvoice(result.invoice_url,async()=>{await refresh();navigate('wallet')})}catch(error){toast(error.message)}}
async function submitTopup(event){event.preventDefault();const file=$('#topup-file').files[0];try{if(file&&(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>3*1024*1024))throw Error('Choose a JPEG, PNG, or WebP image under 3 MB');if(!file)throw Error('Upload your receipt before submitting');const response=await fetch(`/api/wallet/topups/${state.topup.id}/receipt`,{method:'PUT',credentials:'same-origin',headers:{'Content-Type':file.type,'Accept-Language':window.AlartI18n.language},body:file});const body=await response.json();if(!response.ok)throw Error(body.detail||'Receipt upload failed');await post(`wallet/topups/${state.topup.id}/submit`,{reference:$('#topup-reference').value.trim()});state.topup=await api(`wallet/topups/${state.topup.id}`);toast('Receipt submitted for review');render()}catch(error){toast(error.message)}}


function manualOrder(){const current=state.topup;return `${backButton('back-manual','My services')}${heading('Card transfer','Transfer exactly the requested amount, then send the receipt for an operator review.')}<div class="card"><div class="row"><span>Amount</span><strong>${toman(current.amount_minor)}</strong></div><div class="row"><span>Card / destination</span><strong class="break-word">${clean(current.destination)}</strong></div></div><form id="manual-order-receipt"><label class="field">Payment reference <small>(optional)</small><input id="manual-reference" maxlength="200"></label><label class="field">Receipt image<input id="manual-file" type="file" accept="image/jpeg,image/png,image/webp" required></label><p class="muted">Your receipt is sent for manual review. Service delivery starts only after approval.</p><button class="primary full">Submit receipt</button></form>`}
async function submitManualOrder(event){event.preventDefault();const submit=event.target.querySelector('button');if(submit.disabled)return;submit.disabled=true;try{const file=$('#manual-file').files[0];if(!file||!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>3*1024*1024)throw Error('Choose a JPEG, PNG, or WebP image under 3 MB');await post('payments/manual/submit',{order_id:state.topup.orderId,reference:$('#manual-reference').value.trim()||'receipt'});const response=await fetch(`/api/payments/manual/${state.topup.orderId}/receipt`,{method:'PUT',credentials:'same-origin',headers:{'Content-Type':file.type},body:file});const result=await response.json();if(!response.ok)throw Error(result.detail||'Receipt upload failed');toast('Receipt submitted for review');navigate('order');pollOrder(state.topup.orderId)}catch(error){toast(error.message)}finally{submit.disabled=false}}
