const CACHE = 'studyhub-shell-v14';
const SHELL = ['/', '/static/http.js', '/static/reader.js', '/static/i18n.js', '/static/theme.js', '/static/password.js', '/static/validate.js', '/static/app.js', '/static/study.js', '/static/app.css', '/static/icon.svg', '/static/icon-192.png', '/static/icon-512.png', '/manifest.webmanifest'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith('studyhub-shell-') && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener('message', event => { if (event.data === 'ACTIVATE') self.skipWaiting(); });
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  // Never cache passwords, private APIs, notes, or files implicitly.
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || !SHELL.includes(url.pathname)) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); }
    return response;
  }).catch(() => caches.match(event.request)));
});
self.addEventListener('push', event => {
  let data={};try{data=event.data?.json()||{};}catch{}
  const target=new URL(data.url||'/',self.location.origin);
  const voice=typeof data.voice==='string'&&data.voice.startsWith('/api/voices/')?data.voice:'';
  event.waitUntil(Promise.all([
    self.registration.showNotification(data.title||'مساحتي — تذكير',{
      body:data.body||'راجع قائمة مهامك.',icon:'/static/icon-192.png',badge:'/static/icon-192.png',
      tag:data.tag||'studyhub-reminder',data:{url:target.origin===self.location.origin?target.href:self.location.origin,voice},
      dir:'rtl',lang:'ar',requireInteraction:true,renotify:true,vibrate:voice?[300,150,300,150,600]:[200,100,200]
    }),
    // If StudyHub is open on this device, play the stage voice right away.
    voice?self.clients.matchAll({type:'window',includeUncontrolled:true}).then(list=>list.forEach(c=>c.postMessage({type:'play-voice',url:voice,title:data.title||'',body:data.body||''}))):null
  ]));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const target=new URL(event.notification.data?.url||'/',self.location.origin);
  if(target.origin!==self.location.origin)return;
  event.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(async windows=>{
    for(const client of windows){
      if(new URL(client.url).origin===self.location.origin){
        await client.focus();const task=target.searchParams.get('task');
        const voice=event.notification.data?.voice;
        if(voice)client.postMessage({type:'play-voice',url:voice,title:event.notification.title,body:event.notification.body,fromClick:true});
        if(task)client.postMessage({type:'open-task',id:task});return;
      }
    }
    const voice=event.notification.data?.voice;if(voice)target.searchParams.set('voice',voice);
    return self.clients.openWindow(target.href);
  }));
});
