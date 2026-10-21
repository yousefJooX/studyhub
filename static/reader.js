/* StudyHub note reader: KaTeX math, Mermaid diagrams, code highlighting, copy buttons,
   foldable callouts, table of contents and reading progress. Vendor files are self-hosted (CSP: 'self'). */
(function(){
  const loaded = {};
  const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
  function css(href){
    if(loaded[href]) return; loaded[href]=true;
    const l=document.createElement('link'); l.rel='stylesheet'; l.href=href; document.head.appendChild(l);
  }
  function js(src){
    return loaded[src] || (loaded[src]=new Promise((resolve,reject)=>{
      const s=document.createElement('script'); s.src=src; s.onload=resolve; s.onerror=reject; document.head.appendChild(s);
    }));
  }
  async function math(root){
    const nodes=[...root.querySelectorAll('.math:not([data-done])')]; if(!nodes.length) return;
    css('/static/vendor/katex/katex.min.css');
    await js('/static/vendor/katex/katex.min.js');
    for(const el of nodes){
      const display=el.classList.contains('math-display');
      const tex=el.textContent.trim().replace(/^\\[\[(]/,'').replace(/\\[\])]$/,'');
      try{ katex.render(tex,el,{displayMode:display,throwOnError:false,strict:'ignore',output:'html'}); }catch{}
      el.dataset.done='1';
    }
  }
  async function diagrams(root){
    const nodes=[...root.querySelectorAll('pre.mermaid:not([data-processed])')]; if(!nodes.length) return;
    await js('/static/vendor/mermaid.min.js');
    mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:dark()?'dark':'neutral',fontFamily:'Segoe UI, Tahoma, Arial, sans-serif'});
    try{ await mermaid.run({nodes}); }
    catch{ nodes.forEach(n=>n.closest('.mermaid-wrap')?.classList.add('failed')); }
  }
  async function code(root){
    const blocks=[...root.querySelectorAll('.code-block pre code')]; if(!blocks.length) return;
    css('/static/vendor/hljs/github-dark.min.css');
    blocks.forEach(block=>{
      const wrap=block.closest('.code-block'); if(wrap.querySelector('.copy-code')) return;
      const lang=(block.className.match(/language-([\w-]+)/)||[])[1];
      const bar=document.createElement('div'); bar.className='code-bar';
      bar.innerHTML=`<span>${lang?lang.replace(/[<>&"]/g,''):'code'}</span><button type="button" class="copy-code">نسخ</button>`;
      bar.querySelector('button').addEventListener('click',async e=>{
        try{ await navigator.clipboard.writeText(block.textContent); e.target.textContent='اتنسخ ✓'; setTimeout(()=>e.target.textContent='نسخ',1500); }catch{}
      });
      wrap.prepend(bar);
    });
    await js('/static/vendor/hljs/highlight.min.js');
    blocks.forEach(b=>{ if(!b.dataset.highlighted) try{ hljs.highlightElement(b); }catch{} });
  }
  function callouts(root){
    root.querySelectorAll('.callout.foldable > .callout-title').forEach(title=>{
      if(title.dataset.ready) return; title.dataset.ready='1';
      title.setAttribute('role','button'); title.tabIndex=0;
      const box=title.parentElement; title.setAttribute('aria-expanded',String(!box.classList.contains('folded')));
      const toggle=()=>{ box.classList.toggle('folded'); title.setAttribute('aria-expanded',String(!box.classList.contains('folded'))); };
      title.addEventListener('click',toggle);
      title.addEventListener('keydown',e=>{ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); toggle(); } });
    });
    root.querySelectorAll('table').forEach(t=>{
      if(t.parentElement.classList.contains('table-wrap')) return;
      const w=document.createElement('div'); w.className='table-wrap'; t.replaceWith(w); w.appendChild(t);
    });
  }
  function toc(root){
    return [...root.querySelectorAll('h1[id],h2[id],h3[id]')].map(h=>({id:h.id,text:h.textContent,level:Number(h.tagName[1])}));
  }
  function progress(root){
    const bar=document.querySelector('.reading-progress span'); if(!bar) return;
    const update=()=>{
      const r=root.getBoundingClientRect(); const total=r.height-innerHeight;
      bar.style.transform=`scaleX(${total>0?Math.min(1,Math.max(0,-r.top/total)):1})`;
    };
    removeEventListener('scroll',progress.handler); progress.handler=update;
    addEventListener('scroll',update,{passive:true}); update();
  }
  async function enhance(root){
    if(!root) return [];
    callouts(root); progress(root);
    await Promise.allSettled([math(root),diagrams(root),code(root)]);
    return toc(root);
  }
  window.StudyReader={enhance,toc};
  // Standalone /api/vault/note pages
  document.addEventListener('DOMContentLoaded',()=>{ const r=document.querySelector('body.reader-page [data-reader]'); if(r) enhance(r); });
})();
