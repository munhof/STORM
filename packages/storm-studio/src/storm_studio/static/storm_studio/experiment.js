const root=document.querySelector('#experiment-editor'), $=id=>document.getElementById(id);
const read=id=>JSON.parse($(id).textContent), clone=v=>structuredClone(v);
const descriptors=read('experiment-descriptors'),modelSources=read('model-sources');
let doc=read('experiment-document'),selected=null,undo=[],redo=[],problems=[],connecting=null;
const el=(tag,text,parent)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(parent)parent.append(n);return n;};
const status=text=>$('experiment-status').textContent=text;
function invalidate(){$('enqueue').disabled=true;$('batch-preview').textContent='';}
function remember(){undo.push(clone(doc));redo=[];invalidate();}
function position(id,i){if(!doc.visual?.positions?.[id])move(id,[40+i%3*290,50+Math.floor(i/3)*175]);return doc.visual.positions[id];}
function move(id,xy){doc.visual||={};doc.visual.positions||={};doc.visual.positions[id]=xy;}
async function api(action,extra={}){
 const response=await fetch(root.dataset.endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':root.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify({...doc,action,...extra})});
 const result=await response.json();if(!response.ok){problems=result.problems||[];draw();inspect();throw Error(result.error);}return result;
}
function action(id,fn){$(id).onclick=async()=>{try{await fn();}catch(e){status(e.message);}};}
function svg(tag,attrs,parent){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));parent.append(n);return n;}
const nodeLabel=node=>descriptors[node.type]?.descriptor?.label||node.type;
const portY=(node,side,port)=>85+Object.keys(descriptors[node.type][side]).indexOf(port)*24;
function connectPort(node,side,port){
 if(side==='outputs'){connecting={node:node.id,port,type:descriptors[node.type].outputs[port]};status(`Salida ${node.id}.${port} seleccionada. Elegí una entrada; Escape cancela.`);draw();return;}
 if(!connecting){status('Elegí primero el círculo de salida de otro bloque.');return;}
 if(connecting.node===node.id||connecting.type!==descriptors[node.type].inputs[port]){status('Estos puertos no son compatibles. Elegí otra entrada.');return;}
 const target=`${node.id}.${port}`,source=`${connecting.node}.${connecting.port}`;
 const edges=doc.graph.edges.filter(e=>e.target!==target);
 const descendants=new Set([node.id]);let changed=true;
 while(changed){changed=false;for(const edge of edges){const [a]=edge.source.split('.'),[b]=edge.target.split('.');if(descendants.has(a)&&!descendants.has(b)){descendants.add(b);changed=true;}}}
 if(descendants.has(connecting.node)){status('La conexión crearía un ciclo. Elegí otra entrada.');return;}
 remember();doc.graph.edges=[...edges,{source,target}];connecting=null;selected=node.id;draw();inspect();status(`Conectado: ${source} → ${target}`);
}
function draw(){
 const canvas=$('canvas');canvas.replaceChildren();const zoom=doc.visual?.zoom||1;$('zoom').value=zoom;
 const layer=svg('g',{transform:`scale(${zoom})`},canvas);
 for(const edge of doc.graph.edges){
  const [sourceId,sourcePort]=edge.source.split('.'),[targetId,targetPort]=edge.target.split('.');
  const source=doc.graph.nodes.find(n=>n.id===sourceId),target=doc.graph.nodes.find(n=>n.id===targetId);if(!source||!target)continue;
  const a=position(sourceId,doc.graph.nodes.indexOf(source)),b=position(targetId,doc.graph.nodes.indexOf(target));
  const sy=a[1]+portY(source,'outputs',sourcePort),ty=b[1]+portY(target,'inputs',targetPort);
  svg('path',{d:`M${a[0]+240} ${sy} C${a[0]+290} ${sy},${b[0]-50} ${ty},${b[0]} ${ty}`,'data-connection':`${edge.source}→${edge.target}`},layer);
 }
 doc.graph.nodes.forEach((node,i)=>{
  const [x,y]=position(node.id,i),bad=problems.some(p=>p.component===node.id);
  const g=svg('g',{transform:`translate(${x},${y})`,tabindex:0,role:'button','aria-label':`${node.id}: ${node.type}`,'data-node-id':node.id},layer);
  svg('rect',{width:240,height:100+24*Math.max(Object.keys(descriptors[node.type].inputs).length,Object.keys(descriptors[node.type].outputs).length),rx:12,fill:bad?'#fff0ef':selected===node.id?'#e8e7ff':'#f9fbff',stroke:bad?'#a32929':'#5265a6'},g);
  svg('text',{x:14,y:27},g).textContent=node.id;svg('text',{x:14,y:49},g).textContent=nodeLabel(node);
  if(bad)svg('text',{x:14,y:67,fill:'#a32929'},g).textContent='Configuración inválida';
  for(const side of ['inputs','outputs'])for(const port of Object.keys(descriptors[node.type][side])){
   const output=side==='outputs',y=portY(node,side,port),active=connecting?.node===node.id&&connecting.port===port&&output;
   svg('text',{x:output?224:16,y:y+4,'text-anchor':output?'end':'start',fill:'#647085'},g).textContent=port;
   const circle=svg('circle',{cx:output?240:0,cy:y,r:8,fill:active?'#d47724':output?'#6659bb':'#fff',stroke:'#6659bb','stroke-width':2,tabindex:0,role:'button','aria-label':`${output?'Salida del bloque':'Entrada del bloque'} ${node.id}.${port}`},g);
   circle.onpointerdown=e=>e.stopPropagation();
   circle.onclick=e=>{e.stopPropagation();connectPort(node,side,port);};
   circle.onkeydown=e=>{e.stopPropagation();if(['Enter',' '].includes(e.key)){e.preventDefault();connectPort(node,side,port);}};
  }
  g.onclick=()=>{selected=node.id;draw();inspect();};
  g.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selected=node.id;inspect();return;}const d={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]}[e.key];if(d){e.preventDefault();remember();move(node.id,[Math.max(0,x+d[0]),Math.max(0,y+d[1])]);draw();canvas.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`)?.focus();}};
  g.onpointerdown=e=>{if(e.button!==0)return;const start=[e.clientX,e.clientY];let moved=false;g.setPointerCapture(e.pointerId);g.onpointermove=ev=>{if(Math.abs(ev.clientX-start[0])+Math.abs(ev.clientY-start[1])<4&&!moved)return;if(!moved){remember();moved=true;}const xy=[Math.max(0,x+(ev.clientX-start[0])/zoom),Math.max(0,y+(ev.clientY-start[1])/zoom)];move(node.id,xy);g.setAttribute('transform',`translate(${xy})`);};g.onpointerup=()=>{g.onpointermove=null;if(moved){selected=node.id;draw();inspect();}};};
 });
}
function field(parent,label,schema,value,change){
 if(schema.type==='object'&&schema.properties){const group=el('fieldset',undefined,parent);el('legend',label,group);Object.entries(schema.properties).forEach(([k,s])=>field(group,k,s,value?.[k],v=>change({...value,[k]:v})));return;}
 const wrap=el('label',label,parent);let input;
 if(schema.enum){input=el('select',undefined,wrap);el('option','Seleccionar…',input).value='';schema.enum.forEach(v=>el('option',String(v),input).value=v);}
 else if(schema.type==='array'){input=el('textarea',undefined,wrap);input.placeholder='Un valor por línea; coordenadas separadas por comas';}
 else{input=el('input',undefined,wrap);input.type=schema.type==='boolean'?'checkbox':['number','integer'].includes(schema.type)?'number':'text';input.step=schema.type==='integer'?'1':'any';if(schema.minimum!==undefined)input.min=schema.minimum;if(schema.maximum!==undefined)input.max=schema.maximum;}
 const current=value??schema.default;if(schema.type==='array')input.value=(current||[]).map(v=>Array.isArray(v)?v.join(', '):v).join('\n');else if(schema.type==='boolean')input.checked=!!current;else input.value=current??'';
 input.onchange=()=>{let v=input.value;if(schema.type==='boolean')v=input.checked;else if(['number','integer'].includes(schema.type))v=Number(v);else if(schema.type==='array')v=v.split('\n').filter(s=>s.trim()).map(s=>schema.items?.type==='string'?s.trim():s.includes(',')?s.split(',').map(Number):parse(s));change(v);};
}
function unique(base){base=base.replace(/[^a-zA-Z0-9_-]/g,'_');let n=base,i=2;while(doc.graph.nodes.some(v=>v.id===n))n=base+'_'+i++;return n;}
function inspect(){
 const panel=$('inspector');panel.replaceChildren();const node=doc.graph.nodes.find(n=>n.id===selected);if(!node){panel.textContent='Seleccioná un nodo.';return;}
 const d=descriptors[node.type];el('h3',nodeLabel(node),panel);if(d.descriptor?.description)el('p',d.descriptor.description,panel);el('p',`${d.kind} · versión ${d.version}`,panel);
 if(node.type==='model.saved'){
  const label=el('label','Modelo entrenado, bundle o pesos recuperados',panel),select=el('select',undefined,label);
  const empty=el('option','Elegir modelo de una corrida o bundle…',select);empty.value='';
  modelSources.forEach(source=>{const option=el('option',source.label,select);option.value=source.id;});
  const current=node.config?.source;select.value=current?.id||'';
  select.onchange=()=>{remember();const source=modelSources.find(item=>item.id===select.value);if(source){node.config={source:clone(source)};}else node.config={};inspect();draw();};
  if(current){const hint=el('p',`Origen: ${current.label}. Modelo ${current.model} · versión ${current.model_version}.`,panel);hint.className='muted';if(current.input_contract?.shape)el('p',`Forma declarada de entrada: ${JSON.stringify(current.input_contract.shape)}. Agregá la preparación correspondiente antes de inferencia.`,panel);}
  if(current){
   el('button','Agregar rama de inferencia',panel).onclick=()=>{
    remember();const inference={id:unique('inferencia'),type:'model.infer_saved',version:'1',config:{}};
    doc.graph.nodes.push(inference);doc.graph.edges.push({source:node.id+'.model',target:inference.id+'.model'});
    if(current.source_graph){const preparation={id:unique('preparacion_guardada'),type:'adapter.saved_preparation',version:'1',config:{}};
     doc.graph.nodes.push(preparation);doc.graph.edges.push({source:node.id+'.model',target:preparation.id+'.model'},
      {source:preparation.id+'.context',target:inference.id+'.validation'});selected=preparation.id;
    }else selected=inference.id;
    draw();inspect();batches();status('Conectá los datos de validación a la nueva rama y validá la configuración.');
   };
   if(current.capabilities?.some(capability=>['train','group'].includes(capability))){
    el('button','Copiar parámetros para entrenar',panel).onclick=async()=>{try{
     const result=await api('copy_model_parameters',{source:current.id});remember();
     const copied={...result.node,id:unique(current.model+'_nuevo')};doc.graph.nodes.push(copied);selected=copied.id;
     draw();inspect();batches();status('Parámetros copiados. Elegí las entradas de entrenamiento y validación del nuevo modelo.');
    }catch(error){status(error.message);}};
   }
  }

 }
 const previewButton=el('button','Previsualizar nodo',panel);previewButton.hidden=['model','evaluate'].includes(d.kind)||node.type==='model.saved'||Object.values(d.inputs).includes('model');previewButton.onclick=async()=>{previewButton.disabled=true;try{$('context-preview').open=true;$('context-preview').scrollIntoView({behavior:'smooth',block:'nearest'});status('Preparando una muestra en el worker…');const queued=await api('preview_async',{node:node.id});let response;for(let attempt=0;attempt<120;attempt++){await new Promise(resolve=>setTimeout(resolve,1000));response=await api('preview_status',{job:queued.job});if(!['pending','running'].includes(response.status))break;}if(response.status!=='completed')throw Error(response.error||'La muestra sigue pendiente. Consultá Ejecuciones.');const result=response.result;const failures=Object.entries(result.nodes||{}).filter(([,r])=>r.status==='failed');if(failures.length)status(failures.map(([name,r])=>`${name}: ${r.error}`).join(' · '));else status('Muestra preparada; no valida el dataset completo.');$('preview-output').textContent=JSON.stringify(result,null,2);window.StormContextVisuals.preview(result,$('context-comparison'));}catch(e){status(e.message);}finally{previewButton.disabled=false;}};
 for(const [port,type]of Object.entries(d.inputs)){const label=el('label',`Entrada ${port}`,panel),select=el('select',undefined,label);select.setAttribute('aria-label',`Entrada ${port}`);el('option','Elegir salida…',select).value='';doc.graph.nodes.filter(n=>n.id!==node.id).forEach(n=>Object.entries(descriptors[n.type].outputs).filter(([,t])=>t===type).forEach(([p])=>el('option',`${n.id}.${p}`,select).value=`${n.id}.${p}`));select.value=doc.graph.edges.find(e=>e.target===`${node.id}.${port}`)?.source||'';select.onchange=()=>{remember();doc.graph.edges=doc.graph.edges.filter(e=>e.target!==`${node.id}.${port}`);if(select.value)doc.graph.edges.push({source:select.value,target:`${node.id}.${port}`});draw();};}
 el('p',`Salidas: ${Object.keys(d.outputs).join(', ')}`,panel);el('p',`Lee: ${d.reads.join(', ')||'configuración'}. Modifica: ${d.writes.join(', ')||'salida propia'}.`,panel);
 const architecture=d.descriptor?.graph;
 Object.entries(d.schema.properties||{}).forEach(([k,s])=>{if(k===architecture?.field||node.type==='model.saved'&&k==='source')return;field(panel,d.descriptor?.field_labels?.[k]||s.title||k,s,node.config?.[k],v=>{remember();node.config||={};node.config[k]=v;});});
 if(architecture){const detail=el('details',undefined,panel);el('summary','Arquitectura interna del modelo',detail);el('p','Este grafo configura el modelo; las conexiones del experimento se conservan en el canvas.',detail);const renderValue=(label,schema,value,id,update)=>{const box=el('div');field(box,label,schema,value,update);return box;};detail.append(window.StormModelGraph.render(architecture,node.config?.[architecture.field],node.id+'_architecture',value=>{remember();node.config||={};node.config[architecture.field]=value;},renderValue));}
 const contract=d.descriptor?.input_contract;if(contract)el('p',`Entradas: ${contract.input_type}; preparación ${contract.preparation}; requiere ${contract.required_steps.join(', ')||'ningún paso externo'}.`,panel);
 problems.filter(p=>p.component===node.id).forEach(p=>el('p',`${p.field}: ${p.message}`,panel).className='node-problem');
 el('button','Duplicar nodo',panel).onclick=()=>{remember();const n=clone(node);n.id=unique(node.id);doc.graph.nodes.push(n);selected=n.id;draw();inspect();batches();};
 el('button','Eliminar nodo',panel).onclick=()=>{remember();doc.graph.nodes=doc.graph.nodes.filter(n=>n!==node);doc.graph.edges=doc.graph.edges.filter(e=>!e.source.startsWith(node.id+'.')&&!e.target.startsWith(node.id+'.'));selected=null;draw();inspect();batches();};
}
const categories={load:'Fuentes',transform:'Transformaciones',join:'Uniones',adapt:'Adaptadores',model:'Modelos',evaluate:'Métricas'};
const groups={};
Object.entries(descriptors).forEach(([name,d])=>{const category=d.descriptor?.category==='features'?'Extracción de características':d.descriptor?.category==='models'?'Modelos':categories[d.kind]||d.kind;if(!groups[category]){const group=el('div',undefined,$('catalog'));group.className='catalog-group';el('h3',category,group);groups[category]=group;}const button=el('button',d.descriptor?.label||name,groups[category]);button.title=d.descriptor?.description||name;button.dataset.search=(name+' '+(d.descriptor?.label||'')).toLowerCase();button.onclick=()=>{remember();const n={id:unique(name),type:name,config:{}};Object.entries(d.schema.properties||{}).forEach(([k,v])=>{if(v.default!==undefined)n.config[k]=clone(v.default);});doc.graph.nodes.push(n);selected=n.id;draw();inspect();batches();};});
function parse(v){v=v.trim();return v==='true'?true:v==='false'?false:v!==''&&Number.isFinite(Number(v))?Number(v):v;}
function parameterOptions(select){el('option','Parámetro del nodo…',select).value='';function visit(prefix,schema){Object.entries(schema.properties||{}).forEach(([k,s])=>{const path=prefix+'.'+k;if(s.type==='object')visit(path,s);else if(s.type!=='array')el('option',path,select).value=path;});}doc.graph.nodes.forEach(n=>visit(n.id,descriptors[n.type].schema));}
function batches(){
 $('seeds').value=(doc.seeds||[156]).join(', ');$('variants').replaceChildren();$('sweeps').replaceChildren();
 (doc.variants||=[{id:'default',parameters:{}}]).forEach((v,i)=>{const row=el('div',undefined,$('variants')),label=el('label','Variante',row),input=el('input',undefined,label);input.value=v.id;input.onchange=()=>{remember();v.id=input.value;};el('button','Quitar variante',row).onclick=()=>{remember();doc.variants.splice(i,1);batches();};el('button','Parámetro fijo',row).onclick=()=>{remember();v.parameters['']='';batches();};Object.entries(v.parameters).forEach(([path,value])=>{const select=el('select',undefined,row);select.setAttribute('aria-label','Parámetro fijo');parameterOptions(select);select.value=path;const val=el('input',undefined,row);val.setAttribute('aria-label','Valor fijo');val.value=value??'';select.onchange=()=>{remember();delete v.parameters[path];v.parameters[select.value]=parse(val.value);batches();};val.onchange=()=>{remember();v.parameters[path]=parse(val.value);};});});
 Object.entries(doc.sweep||{}).forEach(([path,values])=>{const row=el('div',undefined,$('sweeps')),select=el('select',undefined,row);select.setAttribute('aria-label','Parámetro del barrido');parameterOptions(select);select.value=path;const label=el('label','Valores separados por coma',row),input=el('input',undefined,label);input.value=values.join(', ');select.onchange=()=>{remember();delete doc.sweep[path];doc.sweep[select.value]=values;batches();};input.onchange=()=>{remember();doc.sweep[path]=input.value.split(',').map(parse);};el('button','Quitar barrido',row).onclick=()=>{remember();delete doc.sweep[path];batches();};});
}
$('seeds').onchange=()=>{remember();doc.seeds=$('seeds').value.split(',').map(v=>Number(v.trim()));};
action('add-variant',()=>{remember();doc.variants.push({id:`variant_${doc.variants.length+1}`,parameters:{}});batches();});
action('add-sweep',()=>{remember();doc.sweep||={};doc.sweep['']=[];batches();});
action('example',()=>{remember();doc.graph=read('experiment-example');doc.visual={positions:{},zoom:1};selected=null;problems=[];draw();inspect();batches();});
action('undo',()=>{if(undo.length){redo.push(clone(doc));doc=undo.pop();draw();inspect();batches();invalidate();}});
action('redo',()=>{if(redo.length){undo.push(clone(doc));doc=redo.pop();draw();inspect();batches();invalidate();}});
action('save',async()=>{const r=await api('save');status(`Guardado: revisión ${r.revision} · ${r.fingerprint}`);});
action('validate',async()=>{const r=await api('validate');problems=r.problems;draw();inspect();status(problems.length?problems.map(p=>`${p.component}.${p.field}: ${p.message}`).join(' · '):'Configuración válida. Datos pendientes de comprobar.');});
action('expand',async()=>{const r=await api('expand'),container=$('batch-preview');container.replaceChildren();el('p',`${r.count} corridas · datos pendientes de comprobar`,container);r.runs.forEach(run=>el('p',`${run.variant_id} · semilla ${run.seed} · ${JSON.stringify(run.parameters)} · ${run.run_id}`,container));$('enqueue').disabled=false;});
action('enqueue',async()=>{const r=await api('enqueue');status(`${r.count} trabajos encolados. Consultá Ejecuciones para ver resultados y evidencia.`);$('enqueue').disabled=true;});
function download(name,text,type){const url=URL.createObjectURL(new Blob([text],{type})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
action('export',()=>download('experiment.json',JSON.stringify(doc,null,2),'application/json'));
action('python',async()=>download('experiment.py',(await api('export_python')).source,'text/x-python'));
$('import').onchange=async()=>{try{const v=JSON.parse(await $('import').files[0].text());if(!Array.isArray(v.graph?.nodes)||!Array.isArray(v.graph?.edges))throw Error('Se requiere una especificación de experimento');if(v.graph.nodes.some(n=>!descriptors[n.type]))throw Error('Instalá los plugins declarados antes de importar');remember();doc=v;draw();inspect();batches();status('Importado; guardá para crear una nueva revisión.');}catch(e){status(e.message);}};
$('zoom').oninput=()=>{doc.visual||={};doc.visual.zoom=Number($('zoom').value);draw();};
action('pan-left',()=>$('canvas-scroll').scrollBy({left:-300,behavior:'smooth'}));action('pan-right',()=>$('canvas-scroll').scrollBy({left:300,behavior:'smooth'}));
root.addEventListener('keydown',e=>{if(e.key==='Escape'){connecting=null;draw();status('Conexión cancelada.');}if(e.ctrlKey&&e.key==='z'&&!['INPUT','TEXTAREA'].includes(e.target.tagName)){e.preventDefault();$(e.shiftKey?'redo':'undo').click();}});
draw();inspect();batches();

root.querySelectorAll('[data-freeze-job]').forEach(button=>button.onclick=async()=>{try{const r=await api('freeze',{job:button.dataset.freezeJob,model_node:button.dataset.model});status(`Selección ${r.selection} congelada. Recargá para habilitar la acción separada de test.`);}catch(e){status(e.message);}});
root.querySelectorAll('[data-test-selection]').forEach(button=>button.onclick=async()=>{try{const r=await api('evaluate_test',{selection:Number(button.dataset.testSelection)});status(`Evaluación del test encolada: ${r.job}`);}catch(e){status(e.message);}});

$('catalog-search').oninput=()=>{const query=$('catalog-search').value.toLowerCase().trim();$('catalog').querySelectorAll('button').forEach(button=>button.hidden=!button.dataset.search.includes(query));Object.values(groups).forEach(group=>group.hidden=![...group.querySelectorAll('button')].some(button=>!button.hidden));};

// Layout changes only visual state; node configuration and edges remain intact.
action('arrange',()=>{
 const levels=new Map(),pending=[...doc.graph.nodes];
 while(pending.length){
  const ready=pending.filter(node=>doc.graph.edges.filter(edge=>edge.target.split('.')[0]===node.id).every(edge=>levels.has(edge.source.split('.')[0])));
  if(!ready.length){status('No se puede ordenar un grafo con ciclos o conexiones incompletas. Validá la configuración.');return;}
  for(const node of ready){const parents=doc.graph.edges.filter(edge=>edge.target.split('.')[0]===node.id).map(edge=>levels.get(edge.source.split('.')[0]));levels.set(node.id,parents.length?Math.max(...parents)+1:0);pending.splice(pending.indexOf(node),1);}
 }
 remember();const rows={};for(const node of doc.graph.nodes){const level=levels.get(node.id),row=rows[level]||0;move(node.id,[30+level*290,35+row*175]);rows[level]=row+1;}
 const width=290*(Math.max(0,...levels.values())+1)+30;
 doc.visual.zoom=Math.max(.4,Math.min(1,Math.floor(($('canvas-scroll').clientWidth/width)*10)/10));
 draw();$('canvas-scroll').scrollTo(0,0);status('Distribución ordenada. Guardá para conservarla.');
});

const storedPreview=new URLSearchParams(location.search).get('preview');
if(storedPreview)api('preview_status',{job:storedPreview}).then(response=>{if(response.status!=='completed')throw Error(response.error||'La muestra sigue pendiente.');$('context-preview').open=true;$('preview-output').textContent=JSON.stringify(response.result,null,2);window.StormContextVisuals.preview(response.result,$('context-comparison'));status('Muestra guardada de una revisión anterior; previsualizá de nuevo después de editar.');}).catch(error=>status(error.message));
