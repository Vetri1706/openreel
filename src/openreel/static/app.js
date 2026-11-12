'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const E = escapeHTML;
const state = {project:null, assets:[], projects:[], renders:[], generations:[], selected:0, view:'scenes', dirty:false,
  playing:false, rendered:false, job:null, key:sessionStorage.getItem('openreel-key') || '', urls:new Map(), capabilities:{}};
let toastTimer, playTimer, renderTimer, generationTimer, savePromise;
const limit=(key,fallback)=>state.capabilities.limits?.[key]??fallback;
const uploadLimit=()=>limit('max_upload_bytes',50*1024*1024);
const uploadLabel=()=>`${Math.round(uploadLimit()/1024/1024)} MB`;
const activeGeneration=job=>['queued','generating','finalizing','awaiting_agent'].includes(job.status);
const generationLabel=status=>({queued:'Queued',generating:'Generating',finalizing:'Saving media',awaiting_agent:'Waiting for your AI',completed:'Ready',failed:'Failed',cancelled:'Canceled'}[status]||status);
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
  stopPlayback(); clearTimeout(generationTimer); const data=await api('/projects/'+id); state.project=data.project; state.assets=data.assets; state.renders=data.renders;state.generations=data.generations||[];
  state.selected=0;state.dirty=false;state.rendered=false;state.job=data.renders.find(j=>['queued','rendering'].includes(j.status))||null;
  localStorage.setItem('openreel-project',id);await refreshProjectList();draw();
  if(state.job)pollRender();
  pollGenerations();
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
  $('#save-status').textContent=state.dirty?'Unsaved changes':'All changes saved';$('#save').disabled=!state.dirty;
  $('#rendered-tab').disabled=!state.renders.some(j=>j.status==='completed');
  drawSidebar();drawInspector();drawTimeline();updatePreview();
}
function thumb(s,i){const asset=state.assets.find(a=>a.id===s.asset_id);return `<div class="scene-thumb" style="background:${s.color}">${asset?.kind==='image'?`<img data-asset-image="${E(asset.id)}" alt="">`:''}<span class="thumb-title">${E(s.title_card)}</span><span class="scene-number">${String(i+1).padStart(2,'0')}</span><span class="scene-duration">${s.duration.toFixed(1)}s</span></div>`;}
function drawSidebar(){
  const root=$('#sidebar-content');
  $$('.rail-button[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));
  $('#panel-title').innerHTML={scenes:`Storyboard <span>${state.project.scenes.length}</span>`,assets:`Media <span>${state.assets.length}</span>`,characters:`Characters <span>${state.project.characters.length}</span>`,generation:`Generation <span>${state.generations.length}</span>`}[state.view];
  $('#add-scene').setAttribute('aria-label',{scenes:'Add scene',assets:'Import media',characters:'Add character',generation:'Generate media'}[state.view]);
  if(state.view==='scenes') {
    root.innerHTML=state.project.scenes.map((s,i)=>`<button class="scene-card ${i===state.selected?'selected':''}" data-select="${i}" aria-label="Scene ${i+1}: ${E(s.title)}" aria-pressed="${i===state.selected}">${thumb(s,i)}<div class="scene-card-caption"><h3>${E(s.title)}</h3><span>${s.asset_id?'●':'○'}</span></div></button>`).join('')+'<button class="add-scene-card" data-action="add-scene">＋ Add scene</button>';
  } else if(state.view==='generation') {
    root.innerHTML='<button class="media-button" data-action="generate">＋ Generate media</button><p class="media-caption">Create through your configured ComfyUI workflows, or hand a request to your connected AI.</p>'+generationCards(state.generations);
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
    <p class="media-caption">${visual?E(visual.name):`PNG, JPG, WebP, MP4, MOV, or WebM · up to ${uploadLabel()}`}</p>
    <button class="media-button generate-button" data-action="generate">✧ Generate image or video</button>
    <div id="scene-generations">${generationCards(state.generations.filter(j=>j.request?.scene_id===s.id).slice(0,3))}</div>
    ${visual?'<button class="text-button" data-action="clear-visual">Use color background</button>':''}
    <div class="divider"></div><div class="section-label">Timing & movement</div>
    <div class="field-row"><label>Duration (sec)<input data-field="duration" type="number" min="0.5" max="${limit('max_scene_seconds',60)}" step="0.1" value="${s.duration}"></label><label>Transition<select data-field="transition"><option value="fade" ${s.transition==='fade'?'selected':''}>Fade</option><option value="cut" ${s.transition==='cut'?'selected':''}>Cut</option></select></label></div>
    <label>Camera movement<select data-field="motion">${Object.entries({push_in:'Slow push in',pull_out:'Slow pull out',pan_left:'Pan left',pan_right:'Pan right',still:'Locked off'}).map(([v,n])=>`<option value="${v}" ${s.motion===v?'selected':''}>${n}</option>`).join('')}</select></label>
    ${visual?.kind==='video'?'<p class="media-caption">Imported clips retain their own camera motion and loop to fill the scene.</p>':''}
    <label>Background color<input type="color" data-field="color" value="${s.color}"></label></div>
    <div><div class="divider"></div><div class="section-label">Words on screen</div>
    <label>Title overlay<textarea data-field="title_card" rows="2" maxlength="160" placeholder="Optional title">${E(s.title_card)}</textarea></label>
    <label>Subtitle<textarea data-field="subtitle" rows="2" maxlength="400" placeholder="Optional caption">${E(s.subtitle)}</textarea></label>
    <button class="media-button" data-action="transcribe" ${!audio||!state.capabilities.transcription?.available?'disabled':''}>Transcribe voiceover to word captions</button>
    <p class="media-caption">${state.capabilities.transcription?.available?(audio?'Whisper creates editable word timing from the attached voiceover.':'Attach a voiceover to transcribe it.'):E(state.capabilities.transcription?.reason||state.capabilities.transcription?.description||'Install and configure Whisper on the server to transcribe audio.')}</p>
    ${s.caption_words?.length?`<details class="caption-details"><summary>${s.caption_words.length} timed words · replaces the subtitle</summary><p>${E(s.caption_words.map(w=>w.text).join(' '))}</p><button class="text-button" data-action="edit-captions">Edit words & timing</button><button class="text-button" data-action="clear-captions">Remove timed captions</button></details>`:''}
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
  previewCaption(s,0);
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
    previewCaption(scene(),elapsed);
  },100);
}
function addScene(){if(!state.project)return;if(state.project.scenes.length>=limit('max_scenes',60)){notify('The configured scene limit has been reached.',true);return;}if(total()+4>limit('max_duration_seconds',600)){notify('The configured project duration limit has been reached.',true);return;}stopPlayback();state.project.scenes.push({id:crypto.randomUUID().replaceAll('-',''),title:'A new scene',duration:4,visual_prompt:'',narration:'',subtitle:'',caption_words:[],title_card:'',color:'#233a3e',motion:'push_in',transition:'fade',asset_id:null,audio_asset_id:null,character_ids:[]});state.selected=state.project.scenes.length-1;state.view='scenes';state.rendered=false;markDirty();draw();}

let fileTarget='library';
function chooseFile(target){fileTarget=target;$('#file-input').accept=target==='music'||target==='audio'?'.wav,.mp3,.m4a,.ogg,.flac':target==='visual'?'.png,.jpg,.jpeg,.webp,.mp4,.mov,.webm':'.png,.jpg,.jpeg,.webp,.mp4,.mov,.webm,.wav,.mp3,.m4a,.ogg,.flac';$('#file-input').value='';$('#file-input').click();}
async function upload(file){
  if(!file)return;if(file.size>uploadLimit())throw new Error(`Choose a file no larger than ${uploadLabel()}.`);
  const projectId=state.project.id,sceneId=scene()?.id,target=fileTarget;notify('Importing '+file.name+'…');
  const response=await fetch(`/api/projects/${projectId}/assets?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:headers(false),body:file});
  if(!response.ok){const error=await response.json();throw new Error(error.detail);}
  const asset=await response.json();if(state.project.id!==projectId)return;
  state.assets.push(asset);const selected=state.project.scenes.find(s=>s.id===sceneId);
  if(target==='music'){state.project.music_asset_id=asset.id;markDirty();}
  else if(selected&&target!=='library'){selected[target==='audio'?'audio_asset_id':'asset_id']=asset.id;if(target==='audio')selected.caption_words=[];markDirty();}
  draw();notify('Media imported.');
}
function generationCards(jobs){
  if(!jobs.length)return '<p class="media-caption">No generation requests yet.</p>';
  return jobs.map(job=>`<button class="generation-card" data-generation="${E(job.id)}"><span class="generation-status ${E(job.status)}">${E(generationLabel(job.status))}</span><span class="generation-prompt">${E(job.request?.prompt||'Media request')}</span><small>${E(job.request?.provider||'')} · ${E(job.request?.kind||'media')}${job.asset_id?' · asset saved':''}</small></button>`).join('');
}
function generationDialog(){
  const providers=state.capabilities.generation?.providers||[],s=scene();
  if(!providers.length){notify('Generation capabilities are unavailable. Refresh the studio.',true);return;}
  const preferred=providers.find(p=>p.id==='comfyui'&&p.configured)?.id||'agent';
  modal(`<span class="eyebrow">CREATE A VISUAL</span><h1>From direction to a frame.</h1><p>Generate with a configured ComfyUI workflow, or ask your connected AI to create media with its own tools.</p><form id="generation-form"><label>Generate with<select name="provider" id="generation-provider">${providers.map(p=>`<option value="${E(p.id)}" ${p.id===preferred?'selected':''}>${E(p.name||p.id)}${p.configured?'':' · setup needed'}</option>`).join('')}</select></label><div class="field-row"><label>Media type<select name="kind" id="generation-kind"></select></label><label>Seed<input name="seed" type="number" min="0" max="4294967295" value="42" step="1" required></label></div><label id="workflow-label">Workflow<select name="workflow_id" id="generation-workflow"></select></label><label>Visual direction<textarea name="prompt" rows="4" maxlength="4000" required placeholder="Describe the image, character reference, or video shot…">${E(s?.visual_prompt||'')}</textarea></label><label>Reference image<select name="reference_asset_id" id="generation-reference"><option value="">No reference</option>${state.assets.filter(a=>a.kind==='image').map(a=>`<option value="${E(a.id)}" ${a.id===s?.asset_id?'selected':''}>${E(a.name)}</option>`).join('')}</select></label>${s?`<label class="checkbox-label"><input name="attach_scene" type="checkbox" checked> Attach result to ${E(s.title)}</label>`:'<p>The result will be saved in your media library.</p>'}<p id="generation-provider-note" class="callout"></p><p id="generation-form-error" class="form-error" role="alert" hidden></p><div class="modal-footer"><button id="submit-generation" class="primary">Create request</button></div></form>`);
  const selectProvider=()=>{
    const provider=providers.find(p=>p.id===$('#generation-provider').value),kind=$('#generation-kind');
    kind.innerHTML=(provider.kinds||['image','video']).map(k=>`<option value="${E(k)}">${k==='image'?'Image':'Video'}</option>`).join('');
    $('#workflow-label').hidden=provider.id!=='comfyui';
    $('#generation-provider-note').textContent=provider.id==='agent'?'This creates a saved handoff. OpenReel cannot start a chat or access your AI account. Copy the request to your connected AI, then import its real output.':provider.configured?'The server submits this request to your ComfyUI workflow. Its models, hardware, and any external nodes determine cost and processing time.':'ComfyUI is not configured on this server. Add the server URL and approved workflows, then restart OpenReel. See docs/generation.md.';
    selectKind();
  };
  const selectKind=()=>{
    const provider=providers.find(p=>p.id===$('#generation-provider').value),kind=$('#generation-kind').value;
    const workflows=(provider.workflows||[]).filter(w=>w.kind===kind);
    $('#generation-workflow').innerHTML=workflows.map(w=>`<option value="${E(w.id)}">${E(w.name||w.id)}</option>`).join('');
    $('#submit-generation').disabled=!provider.configured||(provider.id==='comfyui'&&!workflows.length);
    $('#submit-generation').textContent=provider.id==='agent'?'Create AI handoff':'Generate media';
    selectWorkflow();
  };
  const selectWorkflow=()=>{
    const provider=providers.find(p=>p.id===$('#generation-provider').value);
    const workflow=(provider.workflows||[]).find(w=>w.id===$('#generation-workflow').value);
    $('#generation-reference').required=!!workflow?.requires_reference;
  };
  $('#generation-provider').onchange=selectProvider;$('#generation-kind').onchange=selectKind;$('#generation-workflow').onchange=selectWorkflow;selectProvider();
  $('#generation-form').onsubmit=guard(async event=>{
    event.preventDefault();const data=Object.fromEntries(new FormData(event.target)),projectId=state.project.id;
    const button=$('#submit-generation');button.disabled=true;$('#generation-form-error').hidden=true;
    try{await save();const body={provider:data.provider,kind:data.kind,prompt:data.prompt.trim(),seed:Number(data.seed)};
      if(data.provider==='comfyui')body.workflow_id=data.workflow_id;if(data.reference_asset_id)body.reference_asset_id=data.reference_asset_id;
      if(data.attach_scene&&s){body.scene_id=s.id;body.expected_revision=state.project.revision;}
      const job=await api(`/projects/${projectId}/generations`,'POST',body);
      if(state.project.id!==projectId)return;state.generations.unshift(job);drawGenerationPanels();generationStatus(job);pollGenerations();
    }catch(err){if($('#generation-form-error')){$('#generation-form-error').textContent=err.message;$('#generation-form-error').hidden=false;button.disabled=false;}else throw err;}
  });
}
function agentHandoff(job){
  return `Use the connected OpenReel server to complete generation request ${job.id} for project ${job.project_id}. First call get_generation_status and inspect the saved request. Generate an actual ${job.request.kind} using your available media generation tools. Prompt: ${job.request.prompt}\nSeed: ${job.request.seed}${job.request.reference_asset_id?`\nReference asset: ${job.request.reference_asset_id}`:''}\nImport the actual output with upload_asset or import_asset, then call complete_generation with this job_id, its asset_id, and accurate producer/model provenance. Use read_image_asset(asset_id) to inspect image references when supported. If you cannot generate or transfer the media, say so and leave the request awaiting_agent. Do not substitute a placeholder or claim a completed export.`;
}
function generationStatus(job){
  const awaiting=job.status==='awaiting_agent';
  modal(`<div id="generation-status-dialog" data-job-id="${E(job.id)}"><span class="eyebrow">${E(job.request.kind)} GENERATION</span><h1>${E(generationLabel(job.status))}</h1><p>${E(job.request.prompt)}</p><p class="media-caption">${E(job.request.provider)} · seed ${E(job.request.seed)} · request ${E(job.id)}</p>${job.error?`<p class="form-error" role="alert">${E(job.error)}</p>`:''}${job.attachment_error?`<div class="callout">${E(job.attachment_error)} Your result remains in the media library.</div>`:''}${awaiting?`<div class="callout">Your connected AI must generate and return the actual file. This request stays available if you close the studio.</div><label>Request for your AI<textarea id="agent-handoff" rows="7" readonly>${E(agentHandoff(job))}</textarea></label><button id="copy-handoff" class="secondary">Copy request</button><h2>Return the generated result</h2><p>Use an imported ${E(job.request.kind)} or upload the output from your AI. Completing the request records its provenance and attempts to attach it to the saved scene revision.</p><form id="complete-generation-form"><label>Generated asset<select name="asset_id" required><option value="">Select real output</option>${state.assets.filter(a=>a.kind===job.request.kind).map(a=>`<option value="${E(a.id)}">${E(a.name)}</option>`).join('')}</select></label><div class="field-row"><label>Source / provider<input name="provider" required maxlength="100" placeholder="AI client or generation provider"></label><label>Model, if known<input name="model" maxlength="100" placeholder="Optional"></label></div><label>Or upload generated output<input id="generation-result-file" type="file" accept="${job.request.kind==='image'?'.png,.jpg,.jpeg,.webp':'.mp4,.mov,.webm'}"></label><p id="generation-complete-error" class="form-error" role="alert" hidden></p><button id="complete-generation" class="primary">Save generated result</button></form>`:''}${['queued','generating'].includes(job.status)?'<p role="status" class="callout">The provider is processing your request. You can close this dialog and keep editing.</p>':''}${job.asset_id?`<div class="download-actions"><button class="primary" data-use-asset="${E(job.asset_id)}">Use in selected scene</button><button id="view-generation-library" class="secondary">Open media library</button></div><p>${job.attached?'Attached to the requested scene.':'Saved in the media library.'}</p>`:''}${activeGeneration(job)?'<div class="modal-footer"><button id="cancel-generation" class="secondary">Cancel request</button></div>':''}</div>`);
  if(awaiting){
    $('#copy-handoff').onclick=guard(async()=>{try{await navigator.clipboard.writeText(agentHandoff(job));notify('Request copied. Paste it into your connected AI.');}catch{$('#agent-handoff').focus();$('#agent-handoff').select();notify('Select and copy the request above.');}});
    $('#generation-result-file').onchange=event=>{const hasFile=!!event.target.files.length;$('#complete-generation-form select').required=!hasFile;};
    $('#complete-generation-form').onsubmit=guard(async event=>{
      event.preventDefault();const form=event.target,data=Object.fromEntries(new FormData(form)),file=$('#generation-result-file').files[0];
      const button=$('#complete-generation');button.disabled=true;$('#generation-complete-error').hidden=true;
      try{let assetId=data.asset_id;if(file){if(file.size>uploadLimit())throw new Error(`Choose a file no larger than ${uploadLabel()}.`);
          const response=await fetch(`/api/projects/${job.project_id}/assets?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:headers(false),body:file});
          if(!response.ok){const error=await response.json();throw new Error(error.detail);}const asset=await response.json();assetId=asset.id;
          if(state.project.id===job.project_id&&!state.assets.some(a=>a.id===asset.id))state.assets.push(asset);
        }
        const updated=await api(`/generations/${job.id}/complete`,'POST',{asset_id:assetId,provenance:{producer:data.provider,...(data.model?{model:data.model}:{})}});
        await receiveGeneration(updated);generationStatus(updated);
      }catch(err){if($('#generation-complete-error')){$('#generation-complete-error').textContent=err.message;$('#generation-complete-error').hidden=false;button.disabled=false;}else throw err;}
    });
  }
  if($('#cancel-generation'))$('#cancel-generation').onclick=guard(async()=>{const updated=await api(`/generations/${job.id}/cancel`,'POST');await receiveGeneration(updated);generationStatus(updated);});
  if($('#view-generation-library'))$('#view-generation-library').onclick=()=>{state.view='assets';drawSidebar();closeModal();};
}
function drawGenerationPanels(){
  if(state.view==='generation')drawSidebar();
  const root=$('#scene-generations');if(root&&scene())root.innerHTML=generationCards(state.generations.filter(j=>j.request?.scene_id===scene().id).slice(0,3));
}
async function receiveGeneration(job){
  if(state.project?.id!==job.project_id)return;
  const previous=state.generations.find(j=>j.id===job.id);
  state.generations=previous?state.generations.map(j=>j.id===job.id?job:j):[job,...state.generations];
  if(job.status==='completed'&&previous?.status!=='completed'){
    const data=await api(`/projects/${job.project_id}`);if(state.project?.id!==job.project_id)return;state.assets=data.assets;
    if(!state.dirty&&!savePromise&&!state.playing){state.project=data.project;draw();}else notify('Generated media is ready. Save or reconcile your edits before reloading the scene.');
    notify(job.attached?'Generated media attached to the scene.':'Generated media saved in the library.');
  }
  drawGenerationPanels();
}
async function pollGenerations(){
  clearTimeout(generationTimer);const projectId=state.project?.id;if(!projectId)return;
  try{const jobs=await Promise.all(state.generations.filter(activeGeneration).map(j=>api(`/generations/${j.id}`)));
    if(state.project?.id!==projectId)return;
    for(const job of jobs){const previous=state.generations.find(j=>j.id===job.id);await receiveGeneration(job);
      if(previous?.status!==job.status&&$('#generation-status-dialog')?.dataset.jobId===job.id)generationStatus(job);
    }
  }catch{/* Keep saved requests visible during transient network failures. */}
  if(state.project?.id===projectId&&state.generations.some(activeGeneration))generationTimer=setTimeout(pollGenerations,2500);
}
function previewCaption(s,elapsed){
  const text=s.caption_words?.length?(s.caption_words.find(w=>w.start<=elapsed&&w.end>elapsed)?.text||''):s.subtitle;
  $('#preview-subtitle').innerHTML=text?`<span>${E(text)}</span>`:'';
}
async function transcribeScene(){
  const s=scene();if(!s?.audio_asset_id)return;stopPlayback();await save();const projectId=state.project.id,revision=state.project.revision;
  modal(`<span class="eyebrow">WORD CAPTIONS</span><h1>Transcribe the voiceover.</h1><p>Whisper listens to this scene’s attached voiceover. Review its words and timing before exporting.</p><form id="transcribe-form"><label>Language code (optional)<input name="language" placeholder="Auto-detect, or en / ta / …" maxlength="20"></label><p id="transcribe-message" role="status"></p><div class="modal-footer"><button id="start-transcription" class="primary">Transcribe voiceover</button></div></form>`);
  $('#transcribe-form').onsubmit=guard(async event=>{
    event.preventDefault();const language=new FormData(event.target).get('language').trim();$('#start-transcription').disabled=true;$('#transcribe-message').textContent='Transcribing on the server…';
    try{const project=await api(`/projects/${projectId}/scenes/${s.id}/transcribe`,'POST',{expected_revision:revision,...(language?{language}:{})});
      if(state.project.id===projectId){if(!state.dirty){state.project=project;draw();}else notify('Captions saved on the server. Reconcile your pending edits before reloading.');}
      if($('#transcribe-form'))closeModal();notify('Word captions created. Review the transcript and timing.');
    }catch(err){if($('#transcribe-message')){$('#transcribe-message').textContent=err.message;$('#start-transcription').disabled=false;}else throw err;}
  });
}
function captionDialog(){
  const s=scene();if(!s?.caption_words?.length)return;
  modal(`<span class="eyebrow">WORD CAPTIONS</span><h1>Review the timing.</h1><p>Times are seconds from the start of this scene (${s.duration}s). Words must be in order and cannot overlap.</p><form id="caption-form"><div class="caption-table"><div class="caption-row caption-head"><span>Word</span><span>Start</span><span>End</span></div>${s.caption_words.map((w,i)=>`<div class="caption-row"><input aria-label="Word ${i+1}" name="text-${i}" value="${E(w.text)}" required maxlength="200"><input aria-label="Start of word ${i+1}" name="start-${i}" type="number" min="0" max="${s.duration}" step="0.001" value="${w.start}" required><input aria-label="End of word ${i+1}" name="end-${i}" type="number" min="0" max="${s.duration}" step="0.001" value="${w.end}" required></div>`).join('')}</div><p id="caption-error" class="form-error" role="alert" hidden></p><div class="modal-footer"><button class="primary">Apply captions</button></div></form>`);
  $('#caption-form').onsubmit=event=>{event.preventDefault();const data=new FormData(event.target),words=s.caption_words.map((_,i)=>({text:data.get(`text-${i}`).trim(),start:Number(data.get(`start-${i}`)),end:Number(data.get(`end-${i}`))}));
    if(words.some((w,i)=>!w.text||w.end<=w.start||w.end>s.duration||(i&&w.start<words[i-1].end))){$('#caption-error').textContent='Use non-empty words and increasing, non-overlapping times within the scene.';$('#caption-error').hidden=false;return;}
    s.caption_words=words;markDirty();closeModal();drawInspector();updatePreview();
  };
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
    'Gemini CLI':`<h2>Connect Gemini to the same studio.</h2><p>Keep OpenReel running, then add its tools:</p><pre>gemini mcp add --transport http openreel ${E(url)}\ngemini mcp list</pre><p>Or add <code>"openreel": {"httpUrl": "${E(url)}"}</code> under <code>mcpServers</code> in your Gemini CLI settings. Gemini CLI support does not imply a Gemini consumer-app extension.</p><p><a href="https://geminicli.com/docs/tools/mcp-server/" target="_blank" rel="noreferrer">Gemini CLI MCP guide ↗</a></p>`,
    Codex:`<h2>Give Codex the production tools.</h2><p>Keep this studio running. Add the server in your terminal:</p><pre>codex mcp add openreel --url ${E(url)}</pre><p>For a separate local process:</p><pre>codex mcp add openreel -- uv run --directory /absolute/path/to/openreel openreel mcp \\\n  --data-dir /absolute/path/to/.openreel</pre>`,
    'Claude Code':`<h2>Put Claude in the director’s chair.</h2><p>Keep this studio running, then connect it:</p><pre>claude mcp add --transport http openreel ${E(url)}</pre><p>For a separate local process:</p><pre>claude mcp add --transport stdio openreel -- \\\n  uv run --directory /absolute/path/to/openreel openreel mcp --data-dir /absolute/path/to/.openreel</pre>`,
    'ChatGPT / Claude':`<h2>Connect from your AI’s website.</h2><p>Web clients need a reachable HTTPS server or a supported secure tunnel. A localhost URL alone cannot reach your computer from their cloud.</p><pre>export OPENREEL_OWNER_KEY='a-random-secret-at-least-32-characters'\nopenreel studio --public-url https://reel.example.com</pre><p>Forward that HTTPS origin to this local port. Add <code>https://reel.example.com/mcp</code> as a custom MCP server or connector, select OAuth, and approve with your owner key. Platform access and workspace restrictions still apply.</p><p><a href="https://developers.openai.com/api/docs/guides/custom-mcp-server" target="_blank" rel="noreferrer">ChatGPT connection guide ↗</a> · <a href="https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp" target="_blank" rel="noreferrer">Claude connection guide ↗</a></p>`
  };
  modal(`<span class="eyebrow">YOUR AI. YOUR STUDIO.</span><h1>Bring your own director.</h1><p>Use your existing AI session to plan, generate media, edit, and render through MCP. The studio and your AI share the same requests and assets.</p><div class="connection-tabs">${Object.keys(instructions).map(c=>`<button data-client="${c}" class="${c===client?'active':''}">${c}</button>`).join('')}</div>${instructions[client]}<div class="callout">Try: “Inspect OpenReel’s capabilities. Create four scenes, generate their visuals using an available workflow or your own media tools, attach the actual results, then render a 720p MP4.”</div><p>ComfyUI generation needs configured workflows and installed models. AI handoffs need a client that can both generate media and transfer its output. Connecting MCP alone does not give a client image or video generation. OpenReel is an independent integration, not an official marketplace plugin.</p>`);
  $$('[data-client]',$('#modal')).forEach(b=>b.onclick=()=>connectDialog(b.dataset.client));
}
function exportDialog(){
  stopPlayback();if(state.job&&['queued','rendering'].includes(state.job.status)){renderProgress();return;}
  modal(`<span class="eyebrow">THE FINAL CUT</span><h1>Ready when you are.</h1><p>Your film renders on your configured render worker. No watermark, no API credits.</p><div class="export-summary"><div><small>DURATION</small><strong>${total().toFixed(1)} sec</strong></div><div><small>FORMAT</small><strong>${E(state.project.aspect_ratio)}</strong></div><div><small>SCENES</small><strong>${state.project.scenes.length}</strong></div></div><label>Export quality<select id="resolution"><option value="draft">Draft · 360p · fastest</option><option value="720p" selected>HD · 720p</option><option value="1080p">Full HD · 1080p</option></select></label><p>MP4 · H.264 + AAC · ${state.project.fps} fps. Subtitles are burned in and included as a separate SRT file.</p><div class="modal-footer"><button id="start-export" class="primary" ${!scene()?'disabled':''}>Render video ↑</button></div>`);
  $('#start-export').onclick=guard(async()=>{const resolution=$('#resolution').value;$('#start-export').disabled=true;try{await save();state.job=await api(`/projects/${state.project.id}/renders`,'POST',{resolution});renderProgress();pollRender();}catch(err){$('#start-export').disabled=false;throw err;}});
}
function renderProgress(){
  const job=state.job;if(!job)return;
  modal(`<span class="eyebrow">EXPORT</span><h1>${job.status==='completed'?'That’s a wrap.':job.status==='failed'?'The export stopped.':job.status==='canceled'?'Export canceled.':'Making your final cut.'}</h1><p>${job.status==='completed'?'Your MP4 is ready. Download it or play it in the studio.':job.status==='failed'?E(job.error):job.status==='canceled'?'Your project is saved. You can export again whenever you’re ready.':'You can keep editing. This export uses a saved snapshot of your project.'}</p>${['queued','rendering'].includes(job.status)?`<progress max="100" value="${job.progress}"></progress><div class="progress-text"><span>${job.status==='queued'?'Waiting in queue':'Rendering video'}</span><span>${job.progress}%</span></div><div class="modal-footer"><button id="cancel-export" class="secondary">Cancel export</button></div>`:job.status==='completed'?'<div class="download-actions"><button id="download-video" class="primary">Download MP4 ↓</button><button id="download-srt" class="secondary">Subtitles ↓</button><button id="watch-video" class="secondary">Watch film ▶</button></div>':'<div class="modal-footer"><button id="retry-export" class="primary">New export</button></div>'}`);
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
  'generate':generationDialog,'transcribe':transcribeScene,'edit-captions':captionDialog,
  'clear-captions':()=>{scene().caption_words=[];markDirty();drawInspector();updatePreview();},
  'scene-media':()=>chooseFile('visual'),'scene-audio':()=>chooseFile('audio'),
  'clear-visual':()=>{scene().asset_id=null;markDirty();draw();},'clear-audio':()=>{scene().audio_asset_id=null;scene().caption_words=[];markDirty();drawInspector();updatePreview();},
  'move-left':()=>moveScene(-1),'move-right':()=>moveScene(1),
  'delete-scene':()=>{stopPlayback();state.project.scenes.splice(state.selected,1);state.selected=Math.max(0,state.selected-1);markDirty();draw();}
};
function moveScene(direction){const from=state.selected,to=from+direction;if(to<0||to>=state.project.scenes.length)return;stopPlayback();[state.project.scenes[from],state.project.scenes[to]]=[state.project.scenes[to],state.project.scenes[from]];state.selected=to;markDirty();draw();}
document.addEventListener('click',guard(async event=>{
  const target=event.target.closest('button');if(!target)return;
  if(target.dataset.action)await actions[target.dataset.action]?.();
  if(target.dataset.generation){const job=await api('/generations/'+target.dataset.generation);generationStatus(job);}
  if(target.dataset.select!==undefined)selectScene(Number(target.dataset.select));
  if(target.dataset.view){state.view=target.dataset.view;drawSidebar();}
  if(target.dataset.useAsset){const asset=state.assets.find(a=>a.id===target.dataset.useAsset);if(!scene())throw new Error('Add a scene before assigning media.');if(!asset)throw new Error('Reload the media library before assigning this asset.');scene()[asset.kind==='audio'?'audio_asset_id':'asset_id']=asset.id;if(asset.kind==='audio')scene().caption_words=[];markDirty();draw();notify('Media assigned to scene.');}
}));
$('#inspector-content').addEventListener('input',event=>{
  const key=event.target.dataset.field;if(!key||!scene())return;let value=event.target.value;
  if(key==='duration'){value=Number(value);if(!Number.isFinite(value)||value<.5||value>limit('max_scene_seconds',60))return;if(total()-scene().duration+value>limit('max_duration_seconds',600)){notify('This duration exceeds the configured project limit.',true);return;}if(scene().caption_words?.some(w=>w.end>value)){notify('Shorten or remove timed captions before reducing the scene duration.',true);return;}}
  if(key==='title'&&!value.trim())return;scene()[key]=value;markDirty();
  if(['title_card','subtitle','color','motion'].includes(key))updatePreview();
});
$('#inspector-content').addEventListener('change',event=>{if(event.target.dataset.field){drawSidebar();drawTimeline();}if(event.target.id==='scene-characters'){scene().character_ids=[...event.target.selectedOptions].map(o=>o.value);markDirty();}});
$('#project-select').onchange=guard(async event=>{const id=event.target.value;try{await save();await loadProject(id);}catch(err){event.target.value=state.project.id;throw err;}});
$('#new-project').onclick=()=>projectDialog(true);$('#edit-project').onclick=()=>projectDialog();
$('#add-scene').onclick=()=>({scenes:addScene,assets:()=>chooseFile('library'),characters:characterDialog,generation:generationDialog})[state.view]();
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
    const newRequests=(data.generations||[]).some(j=>!state.generations.some(old=>old.id===j.id));state.generations=data.generations||[];drawGenerationPanels();if(newRequests)pollGenerations();
    if(data.project.revision!==state.project.revision||data.assets.length!==state.assets.length){state.project=data.project;state.assets=data.assets;state.selected=Math.min(state.selected,Math.max(0,state.project.scenes.length-1));draw();notify('Studio updated from your AI director.');}
  }catch{/* Keep the current local editor usable during transient connection failures. */}
},5000);
