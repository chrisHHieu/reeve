"""What a previewed page can be asked to do for design work (phase 3).

The preview door already puts a small bridge in every page it serves (`preview_door._BRIDGE`): the
page says where it is and what it was blocked from. This adds three ways for the person to talk
about the page by touching it, switched on and off by the app:

- **point**: the element under the pointer is outlined; a click names it to the app (a CSS path,
  its text, the start of its HTML, its size), which puts it in the composer;
- **pin**: a click names an element and where on it, and the app keeps a numbered comment there;
  the page draws the numbers the app sends back, following the elements as the page moves;
- **text**: a click on a piece of text lets the person retype it in place; the old and new text go
  to the app, which writes the change into the file.

An element is named with where it is on the page, its size, and the style it is drawn in (font,
size, weight, colour, the background behind it, corners), so a comment says what "this" looks like
as well as where it is. A pin whose element is gone after the page changed is reported lost
(`anchors`). An area the person marked in the app (Mark, drawn over the frame) is told back as page
coordinates with the elements under it (`area`), for the picture and the words the mark becomes.

And one way for the page to hand the person a few levers:

- **tweaks**: a page may declare, in `<script type="application/json" id="reeve-tweaks">`, up to 8
  levers, each a CSS custom property on the root (`"css": "--accent"`) or an attribute on `<html>`
  (`"attr": "data-density"`) with 2 to 8 options. The page reports them when it loads, and applies
  an option the app sends, live and for this load only: what is kept is written by the agent.

The page is not trusted. It can send any of these messages at any time, so the app treats every
field as text to show or to search for, never as markup or a path (the file written is the one on
show, and a change is placed only where the old text occurs exactly once). While a mode is on the
page's own clicks are held, so pointing at a link does not follow it.
"""

from __future__ import annotations

DESIGN_BRIDGE = r"""<script>(function(){
if(window.top===window)return;
var mode=null,box=null,editing=null,pins=[],marks=[],tick=0;
var STATE=/^(open|opened|active|show|shown|visible|hidden|closed|is-[\w-]+|has-[\w-]+)$/;
var say=function(m){m.reevePreview=1;try{window.top.postMessage(m,'*');}catch(e){}};
function ours(el){return el&&el.closest&&el.closest('[data-reeve-ui]');}
function pathOf(el){var parts=[];
 for(var n=el;n&&n.nodeType===1&&n!==document.body&&n!==document.documentElement;n=n.parentElement){
  var t=n.tagName.toLowerCase();
  if(n.id){parts.unshift(t+'#'+CSS.escape(n.id));break;}
  var p=n.parentElement,same=p?[].filter.call(p.children,function(c){return c.tagName===n.tagName;}):[n];
  parts.unshift(same.length>1?t+':nth-of-type('+(same.indexOf(n)+1)+')':t);}
 return parts.join(' > ')||'body';}
function hex(c){var m=/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/.exec(c||'');if(!m)return '';
 if(m[4]!==undefined&&+m[4]===0)return 'transparent';
 return '#'+[m[1],m[2],m[3]].map(function(v){var s=(+v).toString(16);return s.length<2?'0'+s:s;}).join('');}
function backOf(el){for(var n=el;n&&n.nodeType===1;n=n.parentElement){var b=hex(getComputedStyle(n).backgroundColor);if(b&&b!=='transparent')return b;}return '#ffffff';}
function styleOf(el){var cs=getComputedStyle(el);
 return {font:(cs.fontFamily||'').split(',')[0].replace(/["']/g,'').trim().slice(0,40),size:Math.round(parseFloat(cs.fontSize)||0),
  weight:String(cs.fontWeight).slice(0,4),color:hex(cs.color),bg:backOf(el),radius:cs.borderRadius==='0px'?'':String(cs.borderRadius).slice(0,20)};}
function info(el){var r=el.getBoundingClientRect(),html=el.outerHTML||'';
 if(html.length>400)html=html.slice(0,400)+'…';
 var cls=[].filter.call(el.classList,function(c){return c.length<40&&!STATE.test(c);}).slice(0,2);
 return {selector:pathOf(el),tag:el.tagName.toLowerCase(),cls:cls.join('.'),
  text:(el.getAttribute('aria-label')||el.textContent||'').trim().replace(/\s+/g,' ').slice(0,80),
  html:html,w:Math.round(r.width),h:Math.round(r.height),x:Math.round(r.left+scrollX),y:Math.round(r.top+scrollY),
  vw:document.documentElement.clientWidth,style:styleOf(el)};}
function frame(){if(box)return box;box=document.createElement('div');box.setAttribute('data-reeve-ui','');
 box.style.cssText='position:fixed;pointer-events:none;z-index:2147483646;border:2px solid #74358c;border-radius:4px;'+
  'box-shadow:0 0 0 3px rgba(116,53,140,.18);transition:left 90ms ease-out,top 90ms ease-out,width 90ms ease-out,height 90ms ease-out;display:none';
 document.documentElement.appendChild(box);return box;}
function outline(el){var b=frame();if(!el){b.style.display='none';return;}var r=el.getBoundingClientRect();
 b.style.display='block';b.style.left=(r.left-3)+'px';b.style.top=(r.top-3)+'px';b.style.width=(r.width+2)+'px';b.style.height=(r.height+2)+'px';}
function picked(e){var el=e.target;if(!el||el.nodeType!==1||ours(el)||el===document.documentElement||el===document.body)return null;return el;}
function textOnly(el){return el&&el.children.length===0&&(el.textContent||'').trim().length>0;}
function edit(el){if(!textOnly(el)){say({type:'notext',el:info(el)});return;}
 var before=el.textContent;editing=el;outline(null);
 el.setAttribute('contenteditable','plaintext-only');el.focus();
 var range=document.createRange();range.selectNodeContents(el);var sel=getSelection();sel.removeAllRanges();sel.addRange(range);
 var done=function(keep){el.removeEventListener('blur',onBlur);el.removeEventListener('keydown',onKey);
  el.removeAttribute('contenteditable');editing=null;
  if(!keep){el.textContent=before;return;}
  var after=el.textContent;if(after!==before)say({type:'retyped',el:info(el),before:before,after:after});};
 var onBlur=function(){done(true);};
 var onKey=function(k){if(k.key==='Enter'&&!k.shiftKey){k.preventDefault();el.blur();}else if(k.key==='Escape'){k.preventDefault();done(false);}};
 el.addEventListener('blur',onBlur);el.addEventListener('keydown',onKey);}
function setCursor(){document.documentElement.style.cursor=mode==='text'?'text':mode?'crosshair':'';}
document.addEventListener('mousemove',function(e){if(!mode||editing)return;var el=picked(e);
 outline(mode==='text'&&!textOnly(el)?null:el);},true);
['mousedown','mouseup','pointerdown','pointerup','dblclick','submit'].forEach(function(kind){
 document.addEventListener(kind,function(e){if(mode&&!(editing&&editing.contains(e.target))&&!ours(e.target)){e.preventDefault();e.stopPropagation();}},true);});
document.addEventListener('click',function(e){if(!mode)return;if(editing&&editing.contains(e.target))return;
 var el=picked(e);if(!el)return;e.preventDefault();e.stopPropagation();
 if(mode==='point')say({type:'picked',el:info(el)});
 else if(mode==='pin'){var r=el.getBoundingClientRect();
  say({type:'pinned',el:info(el),cx:Math.round(e.clientX),cy:Math.round(e.clientY),
   fx:Math.max(0,Math.min(1,(e.clientX-r.left)/Math.max(1,r.width))),fy:Math.max(0,Math.min(1,(e.clientY-r.top)/Math.max(1,r.height)))});}
 else if(mode==='text')edit(el);},true);
var lostSaid=null;
function place(){var lost=[];for(var i=0;i<marks.length;i++){var m=marks[i],pin=pins[i],el=null;
  try{el=document.querySelector(pin.selector);}catch(x){el=null;}
  if(!el){m.style.display='none';lost.push(pin.n);continue;}var r=el.getBoundingClientRect();m.style.display='flex';
  m.style.left=(r.left+r.width*(pin.fx||0)-11)+'px';m.style.top=(r.top+r.height*(pin.fy||0)-22)+'px';}
 var said=lost.join(',');if(said!==lostSaid){lostSaid=said;say({type:'anchors',lost:lost});}
 tick=marks.length?requestAnimationFrame(place):0;}
function area(d){var x=+d.x||0,y=+d.y||0,w=Math.max(1,+d.w||0),h=Math.max(1,+d.h||0),seen=[];
 [[.5,.5],[.2,.2],[.8,.2],[.2,.8],[.8,.8]].forEach(function(p){var el=document.elementFromPoint(x+w*p[0],y+h*p[1]);
  if(!el||ours(el)||el===document.documentElement||el===document.body||seen.indexOf(el)>=0)return;seen.push(el);});
 seen=seen.filter(function(el){return !seen.some(function(o){return o!==el&&el.contains(o);});});
 say({type:'area',id:String(d.id||'').slice(0,40),page:{x:Math.round(x+scrollX),y:Math.round(y+scrollY),w:Math.round(w),h:Math.round(h)},
  vw:document.documentElement.clientWidth,under:seen.slice(0,4).map(info)});}
function drawPins(list){marks.forEach(function(m){m.remove();});marks=[];lostSaid=null;pins=Array.isArray(list)?list.slice(0,40):[];
 pins.forEach(function(pin){var m=document.createElement('div');m.setAttribute('data-reeve-ui','');m.textContent=String(pin.n||'');
  m.style.cssText='position:fixed;z-index:2147483647;pointer-events:none;width:22px;height:22px;border-radius:50% 50% 50% 4px;'+
   'display:flex;align-items:center;justify-content:center;font:700 11px system-ui,sans-serif;box-shadow:0 2px 6px rgba(30,26,36,.3);'+
   (pin.sent?'background:#fff;color:#74358c;border:1.5px solid #74358c':'background:#74358c;color:#fff');
  document.documentElement.appendChild(m);marks.push(m);});
 if(tick)cancelAnimationFrame(tick);tick=marks.length?requestAnimationFrame(place):0;}
var levers=[];
function readTweaks(){var node=document.getElementById('reeve-tweaks');levers=[];if(!node)return;var spec;
 try{spec=JSON.parse(node.textContent||'{}');}catch(x){say({type:'tweaks',levers:[],error:'the tweaks are not valid JSON'});return;}
 var root=document.documentElement;
 Object.keys(spec||{}).slice(0,8).forEach(function(name){var l=spec[name]||{};
  var css=typeof l.css==='string'&&/^--[\w-]+$/.test(l.css)?l.css:null,attr=typeof l.attr==='string'&&/^[a-z][\w-]*$/i.test(l.attr)?l.attr:null;
  var options=Array.isArray(l.options)?l.options.filter(function(o){return typeof o==='string'&&o.length<=60;}).slice(0,8):[];
  if((!css&&!attr)||options.length<2)return;
  var now=css?getComputedStyle(root).getPropertyValue(css).trim():root.getAttribute(attr);
  levers.push({name:String(name).slice(0,40),label:typeof l.label==='string'?l.label.slice(0,40):String(name).slice(0,40),
   css:css,attr:attr,options:options,current:now||options[0]});});
 say({type:'tweaks',levers:levers});}
function tweak(name,value){for(var i=0;i<levers.length;i++){var l=levers[i];if(l.name!==name||l.options.indexOf(value)<0)continue;
 if(l.css)document.documentElement.style.setProperty(l.css,value);else document.documentElement.setAttribute(l.attr,value);}}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',readTweaks);else readTweaks();
window.addEventListener('message',function(e){if(e.source!==window.top)return;var d=e.data||{};
 if(d.reevePreviewDo==='design'){mode=(d.mode==='point'||d.mode==='pin'||d.mode==='text')?d.mode:null;
  if(!mode&&editing)editing.blur();outline(null);setCursor();}
 else if(d.reevePreviewDo==='pins')drawPins(d.pins);
 else if(d.reevePreviewDo==='area')area(d);
 else if(d.reevePreviewDo==='tweak'&&typeof d.name==='string'&&typeof d.value==='string')tweak(d.name,d.value);});
})();</script>"""
