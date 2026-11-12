'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const E = escapeHTML;
const state = {project:null, assets:[], projects:[], renders:[], selected:0, view:'scenes', dirty:false,
  playing:false, rendered:false, job:null, key:sessionStorage.getItem('openreel-key') || '', urls:new Map(), capabilities:{}};
let toastTimer, playTimer, renderTimer, savePromise;
function notify(message, error=false) {
  clearTimeout(toastTimer); const toast=$('#toast'); toast.textContent=message; toast.className='toast'+(error?' error':''); toast.hidden=false;
  toastTimer=setTimeout(()=>toast.hidden=true,error?9000:4200);
}
function headers(json=true) {
  return {'X-OpenReel-Request':'studio',...(json?{'Content-Type':'application/json'}:{}),...(state.key?{Authorization:`Bearer ${state.key}`}:{})};
}
async function api(path, method='GET', body) {
  const response=await fetch('/api'+path,{method,headers:headers(),body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok) {let data;try{data=await response.json();}catch{data={detail:response.statusText};}
    throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));}
  return response.json();
}
async function blobUrl(path) {
  const response=await fetch(path,{headers:headers(false)});
  if(!response.ok) throw new Error('Could not load media. Check your connection or server key.');
  return URL.createObjectURL(await response.blob());
}
function assetUrl(id) {
  if(!state.urls.has(id)) state.urls.set(id,blobUrl('/api/assets/'+encodeURIComponent(id)).catch(err=>{state.urls.delete(id);throw err;}));
  return state.urls.get(id);
}
async function hydrate(root=document) {
  await Promise.all($$('[data-asset-image]',root).map(async img=>{try{img.src=await assetUrl(img.dataset.assetImage);}catch(err){img.alt='Media unavailable';}}));
}
const scene=()=>state.project?.scenes[state.selected];
const total=()=>state.project?.scenes.reduce((t,s)=>t+s.duration,0)||0;
const time=t=>`${String(Math.floor(t/60)).padStart(2,'0')}:${String(Math.floor(t%60)).padStart(2,'0')}`;
const startTime=()=>state.project?.scenes.slice(0,state.selected).reduce((t,s)=>t+s.duration,0)||0;
function markDirty(){state.dirty=true;$('#save-status').textContent='Unsaved changes';$('#save').disabled=false;}
function modal(content){$('#modal-content').innerHTML=content;if(!$('#modal').open)$('#modal').showModal();}
function closeModal(){$('#modal').close();}
function guard(fn){return (...args)=>Promise.resolve().then(()=>fn(...args)).catch(err=>notify(err.message,true));}

async function refreshProjectList() {
  state.projects=await api('/projects');
  $('#project-select').innerHTML=state.projects.map(p=>`<option value="${E(p.id)}">${E(p.title)}</option>`).join('');
  if(state.project)$('#project-select').value=state.project.id;
}
async function loadProject(id) {
  stopPlayback(); const data=await api('/projects/'+id); state.project=data.project; state.assets=data.assets; state.renders=data.renders;
  state.selected=0;state.dirty=false;state.rendered=false;state.job=data.renders.find(j=>['queued','rendering'].includes(j.status))||null;
  localStorage.setItem('openreel-project',id);await refreshProjectList();draw();
  if(state.job)pollRender();
}
async function save() {
  if(savePromise){await savePromise;if(state.dirty)return save();return;}
  if(!state.dirty||!state.project)return;
  const before=JSON.stringify(state.project), id=state.project.id;
  $('#save-status').textContent='Saving…';
  savePromise=(async()=>{
    const saved=await api('/projects/'+id,'PUT',JSON.parse(before));
    if(state.project.id===id){
      const changed=JSON.stringify(state.project)!==before;
      if(changed)state.project.revision=saved.revision;else state.project=saved;
      state.dirty=changed;$('#save-status').textContent=changed?'Unsaved changes':'All changes saved';$('#save').disabled=!changed;
    }
  })();
  try{await savePromise;}catch(err){$('#save-status').textContent='Save failed';throw err;}finally{savePromise=null;}
}
function draw(){
  if(!state.project)return;
  $('#export').disabled=false;
  $('#brief').textContent=state.project.brief||'No brief yet. Add one in project settings.';
  $('#format-badge').innerHTML=`${E(state.project.aspect_ratio)} <span>·</span> ${state.project.fps} FPS`;
  $('#scene-count').textContent=state.project.scenes.length;
  $('#save-status').textContent=state.dirty?'Unsaved changes':'All changes saved';$('#save').disabled=!state.dirty;
  $('#rendered-tab').disabled=!state.renders.some(j=>j.status==='completed');
  drawSidebar();drawInspector();drawTimeline();updatePreview();
}
function thumb(s,i){const asset=state.assets.find(a=>a.id===s.asset_id);return `<div class="scene-thumb" style="background:${s.color}">${asset?.kind==='image'?`<img data-asset-image="${E(asset.id)}" alt="">`:''}<span class="thumb-title">${E(s.title_card)}</span><span class="scene-number">${String(i+1).padStart(2,'0')}</span><span class="scene-duration">${s.duration.toFixed(1)}s</span></div>`;}
function drawSidebar(){
  const root=$('#sidebar-content');
  $$('.rail-button[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));
  $('#panel-title').innerHTML={scenes:`Storyboard <span>${state.project.scenes.length}</span>`,assets:`Media <span>${state.assets.length}</span>`,characters:`Characters <span>${state.project.characters.length}</span>`}[state.view];
  $('#add-scene').setAttribute('aria-label',{scenes:'Add scene',assets:'Import media',characters:'Add character'}[state.view]);
  if(state.view==='scenes') {
    root.innerHTML=state.project.scenes.map((s,i)=>`<button class="scene-card ${i===state.selected?'selected':''}" data-select="${i}" aria-label="Scene ${i+1}: ${E(s.title)}" aria-pressed="${i===state.selected}">${thumb(s,i)}<div class="scene-card-caption"><h3>${E(s.title)}</h3><span>${s.asset_id?'●':'○'}</span></div></button>`).join('')+'<button class="add-scene-card" data-action="add-scene">＋ Add scene</button>';
  } else if(state.view==='assets') {
    root.innerHTML=state.assets.length?state.assets.map(a=>`<article class="asset-card"><div class="asset-thumb">${a.kind==='image'?`<img data-asset-image="${E(a.id)}" alt="${E(a.name)}">`:E(a.kind.toUpperCase())}</div><p title="${E(a.name)}">${E(a.name)}</p><button data-use-asset="${E(a.id)}">Use in selected scene ↗</button></article>`).join(''):'<div class="sidebar-empty"><p>Your media lives here. Import images, video clips, or audio to start building.</p><button class="secondary" data-action="import">Import media</button></div>';
  } else {
    root.innerHTML=state.project.characters.map(c=>`<article class="character-card">${c.reference_asset_id?`<div class="asset-thumb"><img data-asset-image="${E(c.reference_asset_id)}" alt="${E(c.name)} reference"></div>`:''}<h3>${E(c.name)}</h3><p>${E(c.description)}</p><small>SEED ${c.seed}</small></article>`).join('')+'<div class="sidebar-empty"><p>Keep reference images, descriptions, and seeds together for your AI director.</p><button class="secondary" data-action="character">Add character</button></div>';
  }
  hydrate(root);
}
function drawInspector(){
  const s=scene(), root=$('#inspector-content');
  $('#inspector-heading').textContent=s?`Scene ${String(state.selected+1).padStart(2,'0')}`:'No scene selected';
  if(!s){root.innerHTML='<p class="sidebar-empty">Add a scene to start editing its visuals, timing, and captions.</p>';return;}
  const visual=state.assets.find(a=>a.id===s.asset_id), audio=state.assets.find(a=>a.id===s.audio_asset_id);
  root.innerHTML=`<div><div class="section-label">The shot <span>${visual?E(visual.kind):'Title card'}</span></div>
    <label>Scene name<input data-field="title" value="${E(s.title)}" maxlength="120"></label>
    <label>Visual direction<textarea data-field="visual_prompt" rows="3" maxlength="4000" placeholder="Describe the scene for your AI director…">${E(s.visual_prompt)}</textarea></label>
    <button class="media-button" data-action="scene-media">＋ ${visual?'Replace visual':'Import image or clip'}</button>
    <p class="media-caption">${visual?E(visual.name):'PNG, JPG, WebP, MP4, MOV, or WebM · up to 50 MB'}</p>
    ${visual?'<button class="text-button" data-action="clear-visual">Use color background</button>':''}
    <div class="divider"></div><div class="section-label">Timing & movement</div>
    <div class="field-row"><label>Duration (sec)<input data-field="duration" type="number" min="0.5" max="60" step="0.1" value="${s.duration}"></label><label>Transition<select data-field="transition"><option value="fade" ${s.transition==='fade'?'selected':''}>Fade</option><option value="cut" ${s.transition==='cut'?'selected':''}>Cut</option></select></label></div>
    <label>Camera movement<select data-field="motion">${Object.entries({push_in:'Slow push in',pull_out:'Slow pull out',pan_left:'Pan left',pan_right:'Pan right',still:'Locked off'}).map(([v,n])=>`<option value="${v}" ${s.motion===v?'selected':''}>${n}</option>`).join('')}</select></label>
    ${visual?.kind==='video'?'<p class="media-caption">Imported clips retain their own camera motion and loop to fill the scene.</p>':''}
    <label>Background color<input type="color" data-field="color" value="${s.color}"></label></div>
    <div><div class="divider"></div><div class="section-label">Words on screen</div>
    <label>Title overlay<textarea data-field="title_card" rows="2" maxlength="160" placeholder="Optional title">${E(s.title_card)}</textarea></label>
    <label>Subtitle<textarea data-field="subtitle" rows="2" maxlength="400" placeholder="Optional caption">${E(s.subtitle)}</textarea></label>
    <div class="divider"></div><div class="section-label">Voice & sound <span>${audio?'Attached':'No audio'}</span></div>
    <label>Narration script<textarea data-field="narration" rows="2" maxlength="2000" placeholder="Write a voiceover, then import a recording or use Piper through your AI.">${E(s.narration)}</textarea></label>
    <button class="media-button" data-action="scene-audio">＋ ${audio?'Replace voiceover':'Import voiceover'}</button>
    ${audio?`<p class="media-caption">${E(audio.name)}</p><button class="text-button" data-action="clear-audio">Remove voiceover</button>`:'<p class="media-caption">Narration is silent until audio is attached. Piper synthesis is available through MCP when installed.</p>'}
    ${state.project.characters.length?`<div class="divider"></div><label>Characters in this scene<select id="scene-characters" multiple size="${Math.min(4,state.project.characters.length)}">${state.project.characters.map(c=>`<option value="${E(c.id)}" ${s.character_ids.includes(c.id)?'selected':''}>${E(c.name)}</option>`).join('')}</select></label>`:''}
    <div class="divider"></div><div class="scene-actions"><button data-action="move-left" ${state.selected===0?'disabled':''}>← Earlier</button><button data-action="move-right" ${state.selected===state.project.scenes.length-1?'disabled':''}>Later →</button><button class="danger" data-action="delete-scene">Delete</button></div></div>`;
}
function drawTimeline(){
  const seconds=total();$('#timeline-meta').textContent=`${state.project.scenes.length} scenes · ${+seconds.toFixed(1)} seconds`;
  $('#timeline-total').textContent=time(seconds)+':00';
  $('#ruler').innerHTML=Array.from({length:6},(_,i)=>`<span>${time(seconds*i/5)}</span>`).join('');
  $('#video-track').innerHTML=state.project.scenes.map((s,i)=>{const asset=state.assets.find(a=>a.id===s.asset_id);return `<button class="clip ${i===state.selected?'selected':''}" data-select="${i}" style="flex:${s.duration}" aria-label="Select scene ${i+1}">${asset?.kind==='image'?`<img data-asset-image="${E(asset.id)}" alt="">`:''}<span>${String(i+1).padStart(2,'0')} ${E(s.title)}</span><small>${s.duration}s</small></button>`;}).join('');
  hydrate($('#video-track'));const music=state.assets.find(a=>a.id===state.project.music_asset_id);
  $('#audio-track').textContent=music?'♫ '+music.name:'＋ Add background audio';$('#audio-track').classList.toggle('has-audio',!!music);
}
async function updatePreview(){
  const s=scene(), image=$('#preview-image'), clip=$('#preview-clip'), frame=$('#frame');
  $('#storyboard-tab').classList.toggle('active',!state.rendered);$('#rendered-tab').classList.toggle('active',state.rendered);
  $('#download-latest').hidden=!state.rendered;
  $('#rendered-video').hidden=!state.rendered;frame.hidden=!s||state.rendered;$('#empty-preview').hidden=!!s||state.rendered;
  $('#playhead').innerHTML=`${time(startTime())} <span>/ ${time(total())}</span>`;
  $('#play').disabled=!s||state.rendered;
  if(!s||state.rendered){clip.pause();return;}
  frame.className='frame '+(state.project.aspect_ratio==='9:16'?'portrait':state.project.aspect_ratio==='1:1'?'square':'');
  frame.style.background=s.color;$('#preview-title').textContent=s.title_card;
  $('#preview-subtitle').innerHTML=s.subtitle?`<span>${E(s.subtitle)}</span>`:'';
  image.hidden=true;clip.hidden=true;clip.pause();image.style.animation='none';
  const asset=state.assets.find(a=>a.id===s.asset_id), selectedId=s.id;
  if(asset){try{const url=await assetUrl(asset.id);if(scene()?.id!==selectedId||state.rendered)return;
    if(asset.kind==='image'){image.src=url;image.hidden=false;if(state.playing&&s.motion!=='still'){void image.offsetWidth;image.style.animation=`${s.motion} ${s.duration}s linear both`;}}
    else {clip.src=url;clip.hidden=false;if(state.playing)clip.play().catch(()=>{});}
  }catch(err){notify(err.message,true);}}
}
function selectScene(index){stopPlayback();state.selected=Math.max(0,Math.min(index,state.project.scenes.length-1));state.rendered=false;$('#rendered-video').pause();drawSidebar();drawInspector();drawTimeline();updatePreview();}
function stopPlayback(){state.playing=false;clearInterval(playTimer);$('#play').textContent='▶';$('#play').setAttribute('aria-label','Play storyboard');$('#preview-clip').pause();$('#preview-image').style.animationPlayState='paused';}
function play(){
  if(state.playing){stopPlayback();return;}if(!scene())return;
  state.playing=true;$('#play').textContent='Ⅱ';$('#play').setAttribute('aria-label','Pause storyboard');
  let started=performance.now();updatePreview();
  playTimer=setInterval(()=>{let elapsed=(performance.now()-started)/1000;
    if(elapsed>=scene().duration){if(state.selected>=state.project.scenes.length-1){stopPlayback();$('#playhead').innerHTML=`${time(total())} <span>/ ${time(total())}</span>`;return;}
      state.selected++;started=performance.now();elapsed=0;drawSidebar();drawInspector();drawTimeline();updatePreview();}
    $('#playhead').innerHTML=`${time(startTime()+elapsed)} <span>/ ${time(total())}</span>`;
  },100);
}
function addScene(){if(!state.project)return;stopPlayback();state.project.scenes.push({id:crypto.randomUUID().replaceAll('-',''),title:'A new scene',duration:4,visual_prompt:'',narration:'',subtitle:'',title_card:'',color:'#233a3e',motion:'push_in',transition:'fade',asset_id:null,audio_asset_id:null,character_ids:[]});state.selected=state.project.scenes.length-1;state.view='scenes';state.rendered=false;markDirty();draw();}

let fileTarget='library';
function chooseFile(target){fileTarget=target;$('#file-input').accept=target==='music'||target==='audio'?'.wav,.mp3,.m4a,.ogg,.flac':target==='visual'?'.png,.jpg,.jpeg,.webp,.mp4,.mov,.webm':'.png,.jpg,.jpeg,.webp,.mp4,.mov,.webm,.wav,.mp3,.m4a,.ogg,.flac';$('#file-input').value='';$('#file-input').click();}
async function upload(file){
  if(!file)return;if(file.size>50*1024*1024)throw new Error('Choose a file smaller than 50 MB.');
  const projectId=state.project.id,sceneId=scene()?.id,target=fileTarget;notify('Importing '+file.name+'…');
  const response=await fetch(`/api/projects/${projectId}/assets?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:headers(false),body:file});
  if(!response.ok){const error=await response.json();throw new Error(error.detail);}
  const asset=await response.json();if(state.project.id!==projectId)return;
  state.assets.push(asset);const selected=state.project.scenes.find(s=>s.id===sceneId);
  if(target==='music'){state.project.music_asset_id=asset.id;markDirty();}
  else if(selected&&target!=='library'){selected[target==='audio'?'audio_asset_id':'asset_id']=asset.id;markDirty();}
  draw();notify('Media imported.');
}
function projectDialog(isNew=false){
  const p=isNew?{title:'Untitled film',brief:'',aspect_ratio:'16:9',fps:24}:state.project;
  modal(`<span class="eyebrow">${isNew?'A NEW BEGINNING':'PROJECT SETTINGS'}</span><h1>${isNew?'What will you make?':'Set the scene.'}</h1><p>Write a brief for your AI director, or build your storyboard one scene at a time.</p><form id="project-form"><label>Project name<input name="title" value="${E(p.title)}" required maxlength="120"></label><label>The brief<textarea name="brief" rows="3" maxlength="5000" placeholder="A 30-second story about…">${E(p.brief)}</textarea></label><div class="field-row"><label>Format<select name="aspect_ratio">${['16:9','9:16','1:1'].map(r=>`<option ${p.aspect_ratio===r?'selected':''}>${r}</option>`).join('')}</select></label>${isNew?'':`<label>Frame rate<select name="fps"><option ${p.fps===24?'selected':''} value="24">24 FPS</option><option ${p.fps===30?'selected':''} value="30">30 FPS</option></select></label>`}</div><div class="modal-footer"><button class="primary" type="submit">${isNew?'Create project':'Save project'}</button></div></form>`);
  $('#project-form').onsubmit=guard(async event=>{event.preventDefault();const data=Object.fromEntries(new FormData(event.target));
    if(isNew){await save();const created=await api('/projects','POST',data);closeModal();await loadProject(created.id);}
    else{Object.assign(state.project,data,{fps:Number(data.fps)});markDirty();await save();closeModal();await refreshProjectList();draw();}});
}
function characterDialog(){
  modal(`<span class="eyebrow">CAST & CONTINUITY</span><h1>A familiar face.</h1><p>Save a reference for your director to reuse. Reference images and seeds help guide consistency; they do not guarantee identical generated characters.</p><form id="character-form"><label>Name<input name="name" required maxlength="80"></label><label>Description<textarea name="description" rows="3" maxlength="2000" placeholder="Appearance, clothing, personality…"></textarea></label><div class="field-row"><label>Seed<input name="seed" type="number" min="0" max="4294967295" value="42"></label><label>Reference image<select name="reference_asset_id"><option value="">None</option>${state.assets.filter(a=>a.kind==='image').map(a=>`<option value="${E(a.id)}">${E(a.name)}</option>`).join('')}</select></label></div><div class="modal-footer"><button class="primary">Add character</button></div></form>`);
  $('#character-form').onsubmit=guard(async event=>{event.preventDefault();const data=Object.fromEntries(new FormData(event.target));state.project.characters.push({...data,id:crypto.randomUUID().replaceAll('-',''),seed:Number(data.seed),reference_asset_id:data.reference_asset_id||null});markDirty();await save();closeModal();state.view='characters';draw();});
}
function audioDialog(){
  const music=state.assets.find(a=>a.id===state.project.music_asset_id);
  modal(`<span class="eyebrow">THE SOUNDTRACK</span><h1>Give it a rhythm.</h1><p>Background audio loops to fit your film and mixes with scene voiceovers.</p><label>Music track<select id="music-select"><option value="">No background audio</option>${state.assets.filter(a=>a.kind==='audio').map(a=>`<option value="${E(a.id)}" ${music?.id===a.id?'selected':''}>${E(a.name)}</option>`).join('')}</select></label><label>Music volume <span id="volume-label">${Math.round(state.project.music_volume*100)}%</span><input id="music-volume" type="range" min="0" max="1" step="0.01" value="${state.project.music_volume}"></label><div class="modal-footer"><button id="upload-music" class="secondary">Import audio</button><button id="save-music" class="primary">Apply</button></div>`);
  $('#music-volume').oninput=e=>$('#volume-label').textContent=Math.round(e.target.value*100)+'%';
  $('#upload-music').onclick=()=>{closeModal();chooseFile('music');};
  $('#save-music').onclick=()=>{state.project.music_asset_id=$('#music-select').value||null;state.project.music_volume=Number($('#music-volume').value);markDirty();closeModal();drawTimeline();};
}
function connectDialog(client='Codex'){
  const url=location.origin+'/mcp';
  const instructions={
    Codex:`<h2>Give Codex the production tools.</h2><p>Keep this studio running. Add the server in your terminal:</p><pre>codex mcp add openreel --url ${E(url)}</pre><p>For a separate local process:</p><pre>codex mcp add openreel -- uv run --directory /absolute/path/to/openreel openreel mcp \\\n  --data-dir /absolute/path/to/.openreel</pre>`,
    'Claude Code':`<h2>Put Claude in the director’s chair.</h2><p>Keep this studio running, then connect it:</p><pre>claude mcp add --transport http openreel ${E(url)}</pre><p>For a separate local process:</p><pre>claude mcp add --transport stdio openreel -- \\\n  uv run --directory /absolute/path/to/openreel openreel mcp --data-dir /absolute/path/to/.openreel</pre>`,
    'ChatGPT / Claude':`<h2>Connect from your AI’s website.</h2><p>Web clients need a reachable HTTPS server or a supported secure tunnel. A localhost URL alone cannot reach your computer from their cloud.</p><pre>export OPENREEL_OWNER_KEY='a-random-secret-at-least-32-characters'\nopenreel studio --public-url https://reel.example.com</pre><p>Forward that HTTPS origin to this local port. Add <code>https://reel.example.com/mcp</code> as a custom MCP server or connector, select OAuth, and approve with your owner key. Platform access and workspace restrictions still apply.</p><p><a href="https://developers.openai.com/api/docs/guides/custom-mcp-server" target="_blank" rel="noreferrer">ChatGPT connection guide ↗</a> · <a href="https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp" target="_blank" rel="noreferrer">Claude connection guide ↗</a></p>`
  };
  modal(`<span class="eyebrow">YOUR AI. YOUR STUDIO.</span><h1>Bring your own director.</h1><p>Use your existing AI session to plan, edit, and render. OpenReel does not need an LLM API key.</p><div class="connection-tabs">${Object.keys(instructions).map(c=>`<button data-client="${c}" class="${c===client?'active':''}">${c}</button>`).join('')}</div>${instructions[client]}<div class="callout">Try: “Use OpenReel to make a 15-second title-card film about slowing down. Write four scenes, add subtitles and camera movement, then render a 720p MP4.”</div><p>Image animation and local rendering are included. Generative video models and AI image synthesis are not installed in this MVP.</p>`);
  $$('[data-client]',$('#modal')).forEach(b=>b.onclick=()=>connectDialog(b.dataset.client));
}
function exportDialog(){
  stopPlayback();if(state.job&&['queued','rendering'].includes(state.job.status)){renderProgress();return;}
  modal(`<span class="eyebrow">THE FINAL CUT</span><h1>Ready when you are.</h1><p>Your film renders on this machine. No watermark, no API credits.</p><div class="export-summary"><div><small>DURATION</small><strong>${total().toFixed(1)} sec</strong></div><div><small>FORMAT</small><strong>${E(state.project.aspect_ratio)}</strong></div><div><small>SCENES</small><strong>${state.project.scenes.length}</strong></div></div><label>Export quality<select id="resolution"><option value="draft">Draft · 360p · fastest</option><option value="720p" selected>HD · 720p</option><option value="1080p">Full HD · 1080p</option></select></label><p>MP4 · H.264 + AAC · ${state.project.fps} fps. Subtitles are burned in and included as a separate SRT file.</p><div class="modal-footer"><button id="start-export" class="primary" ${!scene()?'disabled':''}>Render video ↑</button></div>`);
  $('#start-export').onclick=guard(async()=>{const resolution=$('#resolution').value;$('#start-export').disabled=true;try{await save();state.job=await api(`/projects/${state.project.id}/renders`,'POST',{resolution});renderProgress();pollRender();}catch(err){$('#start-export').disabled=false;throw err;}});
}
function renderProgress(){
  const job=state.job;if(!job)return;
  modal(`<span class="eyebrow">EXPORT</span><h1>${job.status==='completed'?'That’s a wrap.':job.status==='failed'?'The export stopped.':job.status==='canceled'?'Export canceled.':'Making your final cut.'}</h1><p>${job.status==='completed'?'Your MP4 is ready. Download it or play it in the studio.':job.status==='failed'?E(job.error):job.status==='canceled'?'Your project is saved. You can export again whenever you’re ready.':'You can keep editing. This export uses a saved snapshot of your project.'}</p>${['queued','rendering'].includes(job.status)?`<progress max="100" value="${job.progress}"></progress><div class="progress-text"><span>${job.status==='queued'?'Waiting in queue':'Rendering on your machine'}</span><span>${job.progress}%</span></div><div class="modal-footer"><button id="cancel-export" class="secondary">Cancel export</button></div>`:job.status==='completed'?'<div class="download-actions"><button id="download-video" class="primary">Download MP4 ↓</button><button id="download-srt" class="secondary">Subtitles ↓</button><button id="watch-video" class="secondary">Watch film ▶</button></div>':'<div class="modal-footer"><button id="retry-export" class="primary">New export</button></div>'}`);
  if($('#cancel-export'))$('#cancel-export').onclick=guard(async()=>{state.job=await api(`/renders/${job.id}/cancel`,'POST');notify('Canceling export…');});
  if($('#download-video'))$('#download-video').onclick=guard(()=>download(`/api/renders/${job.id}/video.mp4`,state.project.title+'.mp4'));
  if($('#download-srt'))$('#download-srt').onclick=guard(()=>download(`/api/renders/${job.id}/subtitles.srt`,'subtitles.srt'));
  if($('#watch-video'))$('#watch-video').onclick=guard(async()=>{closeModal();await showRendered(job);});
  if($('#retry-export'))$('#retry-export').onclick=()=>{state.job=null;exportDialog();};
}
async function pollRender(){
  clearTimeout(renderTimer);if(!state.job)return;
  try{const id=state.job.id;const job=await api('/renders/'+id);if(state.job?.id!==id)return;state.job=job;
    $('#export').textContent=['queued','rendering'].includes(job.status)?`Rendering ${job.progress}%`:'Export video ↑';
    if($('#modal').open&&$('#modal-content progress'))renderProgress();
    if(['queued','rendering'].includes(job.status)){renderTimer=setTimeout(pollRender,1200);}
    else{if(!state.renders.some(r=>r.id===job.id))state.renders.unshift(job);else state.renders=state.renders.map(r=>r.id===job.id?job:r);
      $('#rendered-tab').disabled=!state.renders.some(r=>r.status==='completed');
      notify(job.status==='completed'?'Your film is ready. Open Latest export to watch.':job.status==='failed'?job.error:'Export canceled.',job.status==='failed');}
  }catch(err){notify(err.message,true);renderTimer=setTimeout(pollRender,4000);}
}
async function showRendered(job){
  job=job||state.renders.find(r=>r.status==='completed');if(!job)return;stopPlayback();
  const url=await blobUrl(`/api/renders/${job.id}/video.mp4`),video=$('#rendered-video');
  if(video.dataset.blob)URL.revokeObjectURL(video.dataset.blob);video.dataset.blob=url;video.src=url;state.rendered=true;updatePreview();
  if(job.revision!==state.project.revision)notify('This export is from project revision '+job.revision+'. Export again to include recent edits.');
}
async function download(path,name){const url=await blobUrl(path),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}

const actions={
  'add-scene':addScene,'import':()=>chooseFile('library'),'character':characterDialog,
  'scene-media':()=>chooseFile('visual'),'scene-audio':()=>chooseFile('audio'),
  'clear-visual':()=>{scene().asset_id=null;markDirty();draw();},'clear-audio':()=>{scene().audio_asset_id=null;markDirty();drawInspector();},
  'move-left':()=>moveScene(-1),'move-right':()=>moveScene(1),
  'delete-scene':()=>{stopPlayback();state.project.scenes.splice(state.selected,1);state.selected=Math.max(0,state.selected-1);markDirty();draw();}
};
function moveScene(direction){const from=state.selected,to=from+direction;if(to<0||to>=state.project.scenes.length)return;stopPlayback();[state.project.scenes[from],state.project.scenes[to]]=[state.project.scenes[to],state.project.scenes[from]];state.selected=to;markDirty();draw();}
document.addEventListener('click',guard(async event=>{
  const target=event.target.closest('button');if(!target)return;
  if(target.dataset.action)actions[target.dataset.action]?.();
  if(target.dataset.select!==undefined)selectScene(Number(target.dataset.select));
  if(target.dataset.view){state.view=target.dataset.view;drawSidebar();}
  if(target.dataset.useAsset){const asset=state.assets.find(a=>a.id===target.dataset.useAsset);if(!scene())throw new Error('Add a scene before assigning media.');scene()[asset.kind==='audio'?'audio_asset_id':'asset_id']=asset.id;markDirty();draw();notify('Media assigned to scene.');}
}));
$('#inspector-content').addEventListener('input',event=>{
  const key=event.target.dataset.field;if(!key||!scene())return;let value=event.target.value;
  if(key==='duration'){value=Number(value);if(!Number.isFinite(value)||value<.5||value>60)return;}
  if(key==='title'&&!value.trim())return;scene()[key]=value;markDirty();
  if(['title_card','subtitle','color','motion'].includes(key))updatePreview();
});
$('#inspector-content').addEventListener('change',event=>{if(event.target.dataset.field){drawSidebar();drawTimeline();}if(event.target.id==='scene-characters'){scene().character_ids=[...event.target.selectedOptions].map(o=>o.value);markDirty();}});
$('#project-select').onchange=guard(async event=>{const id=event.target.value;try{await save();await loadProject(id);}catch(err){event.target.value=state.project.id;throw err;}});
$('#new-project').onclick=()=>projectDialog(true);$('#edit-project').onclick=()=>projectDialog();
$('#add-scene').onclick=()=>({scenes:addScene,assets:()=>chooseFile('library'),characters:characterDialog})[state.view]();
$('#empty-add').onclick=addScene;$('#save').onclick=guard(async()=>{await save();notify('Project saved.');});
$('#previous').onclick=()=>selectScene(state.selected-1);$('#next').onclick=()=>selectScene(state.selected+1);$('#play').onclick=play;
$('#file-input').onchange=guard(event=>upload(event.target.files[0]));
$('#audio-track').onclick=audioDialog;$('#connect-ai').onclick=()=>connectDialog();$('#help').onclick=()=>connectDialog();
$('#export').onclick=exportDialog;$('#download-latest').onclick=()=>{state.job=state.renders.find(j=>j.status==='completed');renderProgress();};$('#close-modal').onclick=closeModal;
$('#storyboard-tab').onclick=()=>{state.rendered=false;$('#rendered-video').pause();updatePreview();};$('#rendered-tab').onclick=guard(()=>showRendered());
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
document.addEventListener('keydown',event=>{if(event.code==='Space'&&!['INPUT','TEXTAREA','SELECT','BUTTON'].includes(document.activeElement.tagName)&&!$('#modal').open){event.preventDefault();play();}if((event.metaKey||event.ctrlKey)&&event.key==='s'){event.preventDefault();guard(save)();}});
async function boot(){
  const health=await api('/health');
  if(health.authentication&&!state.key){modal('<h1>Open your studio.</h1><p>Enter the owner key configured on this server. It stays in this browser tab’s session.</p><form id="login-form"><label>Server owner key<input id="owner-key" type="password" required autocomplete="current-password"></label><button class="primary">Open studio</button></form>');
    $('#login-form').onsubmit=guard(async event=>{event.preventDefault();state.key=$('#owner-key').value;try{await api('/capabilities');sessionStorage.setItem('openreel-key',state.key);closeModal();await boot();}catch(err){state.key='';throw err;}});return;}
  try{state.capabilities=await api('/capabilities');}catch(err){state.key='';sessionStorage.removeItem('openreel-key');if(health.authentication){await boot();return;}throw err;}
  await refreshProjectList();
  if(!state.projects.length){modal('<span class="eyebrow">WELCOME TO OPENREEL</span><h1>Your story.<br>Your studio.</h1><p>A local video workspace for you and your AI. Start with an illustrated demo, or bring your own idea.</p><div class="download-actions"><button id="start-demo" class="primary">Open demo film ↗</button><button id="start-blank" class="secondary">Start from scratch</button></div>');
    $('#start-demo').onclick=guard(async()=>{$('#start-demo').disabled=true;const p=await api('/demo','POST');closeModal();await loadProject(p.id);});$('#start-blank').onclick=()=>projectDialog(true);return;}
  const remembered=localStorage.getItem('openreel-project');await loadProject(state.projects.some(p=>p.id===remembered)?remembered:state.projects[0].id);
}
guard(boot)();
setInterval(async()=>{
  if(!state.project||state.dirty||savePromise||state.playing||$('#modal').open)return;
  try{const id=state.project.id;const data=await api('/projects/'+id);if(state.project.id!==id||state.dirty)return;
    state.renders=data.renders;$('#rendered-tab').disabled=!state.renders.some(j=>j.status==='completed');
    if(data.project.revision!==state.project.revision||data.assets.length!==state.assets.length){state.project=data.project;state.assets=data.assets;state.selected=Math.min(state.selected,Math.max(0,state.project.scenes.length-1));draw();notify('Studio updated from your AI director.');}
  }catch{/* Keep the current local editor usable during transient connection failures. */}
},5000);
